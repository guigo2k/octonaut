from agent.config import Config
from agent.db import Trade, TradeMemory, session_scope
from agent.kraken import KrakenResult
from agent.runner import run_once


def _config():
    return Config.model_validate({
        "strategy": {"type": "GRID", "ticker": "BTCUSD", "balance": 50000,
                      "prompt": "Trade conservatively."},
    })


def _stub_kraken(order_id="o-1"):
    calls = []
    status_calls = []

    def fn(args):
        calls.append(args)
        if args == ["paper", "status"]:
            status_calls.append(args)
            if len(status_calls) == 1:
                # ensure_paper's own probe: simulate a fresh, uninitialized account.
                return KrakenResult(False, None, "not initialized", 1, "paper status")
            return KrakenResult(True, {"current_value": "50000"}, None, 0, "paper status")
        if args[:2] == ["paper", "init"]:
            return KrakenResult(True, {"ok": True}, None, 0, "paper init")
        if args[0] == "ticker":
            return KrakenResult(True, {"XXBTZUSD": {"c": ["60000.0"]}}, None, 0, "ticker")
        if args == ["paper", "balance"]:
            return KrakenResult(
                True, {"balances": {"USD": {"total": "50000"}, "BTC": {"total": "0"}}},
                None, 0, "paper balance",
            )
        if args[:2] in (["paper", "buy"], ["paper", "sell"]):
            return KrakenResult(True, {"order_id": order_id}, None, 0, " ".join(args))
        raise AssertionError(f"unexpected kraken call: {args}")

    return fn, calls


def _stub_reason(proposal: dict):
    seen_states = []

    def fn(state):
        seen_states.append(state)
        return {"proposal": proposal}

    return fn, seen_states


def test_run_once_initializes_paper_account(engine):
    kraken_fn, calls = _stub_kraken()
    reason_fn, _ = _stub_reason({"action": "hold", "size": "0", "rationale": "no signal"})

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    assert ["paper", "init", "--balance", "50000"] in calls


def test_run_once_passes_loaded_skills_and_prompt_to_reason(engine):
    kraken_fn, _ = _stub_kraken()
    reason_fn, seen_states = _stub_reason({"action": "hold", "size": "0", "rationale": "x"})

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    assert len(seen_states) == 1
    assert "kraken-grid-trading" in seen_states[0]["skills_text"]
    assert seen_states[0]["prompt"] == "Trade conservatively."


def test_run_once_persists_trade_and_memory_on_execution(engine):
    kraken_fn, _ = _stub_kraken(order_id="o-42")
    reason_fn, _ = _stub_reason({"action": "buy", "size": "0.05", "rationale": "breakout"})

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    with session_scope(engine) as s:
        trades = s.query(Trade).all()
        memories = s.query(TradeMemory).all()
        assert len(trades) == 1
        assert trades[0].kraken_order_id == "o-42"
        assert trades[0].pair == "BTCUSD"
        assert len(memories) == 1
        assert memories[0].rationale == "breakout"


def test_run_once_persists_nothing_when_rejected(engine):
    kraken_fn, _ = _stub_kraken()
    reason_fn, _ = _stub_reason({"action": "hold", "size": "0", "rationale": "no signal"})

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    with session_scope(engine) as s:
        assert s.query(Trade).count() == 0
        assert s.query(TradeMemory).count() == 0


def test_run_once_attaches_langfuse_callback_when_configured(engine, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_ADDRESS", "http://localhost:1")
    kraken_fn, _ = _stub_kraken()
    seen_configs = []

    def reason_fn(state, config=None):
        seen_configs.append(config)
        return {"proposal": {"action": "hold", "size": "0", "rationale": "x"}}

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    assert len(seen_configs) == 1
    assert len(seen_configs[0]["callbacks"].handlers) == 1
    assert "run_id" in seen_configs[0]["metadata"]


def test_run_once_omits_callbacks_when_langfuse_not_configured(engine, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    kraken_fn, _ = _stub_kraken()
    seen_configs = []

    def reason_fn(state, config=None):
        seen_configs.append(config)
        return {"proposal": {"action": "hold", "size": "0", "rationale": "x"}}

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    assert len(seen_configs) == 1
    assert seen_configs[0]["callbacks"].handlers == []


def test_run_once_tags_trace_with_session_and_user_id(engine):
    kraken_fn, _ = _stub_kraken()
    seen_configs = []

    def reason_fn(state, config=None):
        seen_configs.append(config)
        return {"proposal": {"action": "hold", "size": "0", "rationale": "x"}}

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn, session_id="sess-abc")

    metadata = seen_configs[0]["metadata"]
    assert metadata["langfuse_session_id"] == "sess-abc"
    assert metadata["langfuse_user_id"] == "BTCUSD-GRID"


def test_run_once_defaults_session_id_when_not_given(engine):
    kraken_fn, _ = _stub_kraken()
    seen_configs = []

    def reason_fn(state, config=None):
        seen_configs.append(config)
        return {"proposal": {"action": "hold", "size": "0", "rationale": "x"}}

    run_once(_config(), engine, reason_fn, run_kraken_fn=kraken_fn)

    assert seen_configs[0]["metadata"]["langfuse_session_id"]
