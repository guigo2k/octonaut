from decimal import Decimal

from langchain_core.callbacks.base import BaseCallbackHandler

from agent.graph import (
    Proposal,
    _equity_from_status,
    _holding_size,
    _price_from_ticker,
    build_graph,
    solvency_guard,
)
from agent.kraken import KrakenResult

# --------------------------------------------------------------------------- #
# Pure parsing helpers (real shapes, verified against kraktopus/agent's own
# live-CLI-verified parsing -- see agent/graph.py's module docstring)
# --------------------------------------------------------------------------- #


def test_price_from_ticker_real_shape():
    payload = {"XXBTZUSD": {"c": ["60000.0", "0.01"]}}
    assert _price_from_ticker(payload) == Decimal("60000.0")


def test_equity_from_status_real_shape():
    payload = {"current_value": "50000.00"}
    assert _equity_from_status(payload) == Decimal("50000.00")


def test_holding_size_real_shape():
    payload = {"balances": {"USD": {"total": "40000"}, "BTC": {"total": "0.25"}}}
    assert _holding_size(payload, "BTCUSD") == Decimal("0.25")


def test_holding_size_defaults_to_zero_when_absent():
    payload = {"balances": {"USD": {"total": "50000"}}}
    assert _holding_size(payload, "BTCUSD") == Decimal("0")


# --------------------------------------------------------------------------- #
# solvency_guard -- the safety-critical deterministic piece
# --------------------------------------------------------------------------- #


def test_guard_rejects_hold():
    ok, reason = solvency_guard(
        Proposal(action="hold", size=Decimal("0"), rationale="wait"),
        balance=Decimal("50000"), price=Decimal("60000"), held=Decimal("0"),
    )
    assert not ok and reason == "hold"


def test_guard_allows_buy_within_balance():
    ok, reason = solvency_guard(
        Proposal(action="buy", size=Decimal("0.1"), rationale="breakout"),
        balance=Decimal("50000"), price=Decimal("60000"), held=Decimal("0"),
    )
    assert ok and reason is None


def test_guard_rejects_buy_exceeding_balance():
    ok, reason = solvency_guard(
        Proposal(action="buy", size=Decimal("10"), rationale="yolo"),
        balance=Decimal("50000"), price=Decimal("60000"), held=Decimal("0"),
    )
    assert not ok and "insufficient balance" in reason


def test_guard_allows_sell_within_held():
    ok, reason = solvency_guard(
        Proposal(action="sell", size=Decimal("0.1"), rationale="take profit"),
        balance=Decimal("50000"), price=Decimal("60000"), held=Decimal("0.5"),
    )
    assert ok and reason is None


def test_guard_rejects_sell_exceeding_held():
    ok, reason = solvency_guard(
        Proposal(action="sell", size=Decimal("1"), rationale="exit"),
        balance=Decimal("50000"), price=Decimal("60000"), held=Decimal("0.1"),
    )
    assert not ok and "insufficient position" in reason


# --------------------------------------------------------------------------- #
# Graph wiring -- reason_fn and run_kraken_fn are both injected, so this
# never touches a real LLM or the kraken binary.
# --------------------------------------------------------------------------- #


def _stub_kraken(order_id="o-1"):
    calls = []

    def fn(args):
        calls.append(args)
        if args[0] == "ticker":
            return KrakenResult(True, {"XXBTZUSD": {"c": ["60000.0", "0.01"]}}, None, 0, "ticker")
        if args == ["paper", "status"]:
            return KrakenResult(True, {"current_value": "50000"}, None, 0, "paper status")
        if args == ["paper", "balance"]:
            return KrakenResult(
                True, {"balances": {"USD": {"total": "50000"}, "BTC": {"total": "0.2"}}},
                None, 0, "paper balance",
            )
        if args[:2] in (["paper", "buy"], ["paper", "sell"]):
            return KrakenResult(True, {"order_id": order_id}, None, 0, " ".join(args))
        raise AssertionError(f"unexpected kraken call: {args}")

    return fn, calls


def _stub_reason(proposal: dict):
    def fn(state):
        return {"proposal": proposal}

    return fn


def _initial_state():
    return {"ticker": "BTCUSD", "prompt": "trade conservatively", "skills_text": "",
            "memory": []}


def test_buy_within_balance_executes_and_places_order():
    kraken_fn, calls = _stub_kraken()
    reason_fn = _stub_reason({"action": "buy", "size": "0.05", "rationale": "breakout"})
    graph = build_graph(reason_fn, run_kraken_fn=kraken_fn)

    result = graph.invoke(_initial_state())

    assert result["decision"] == "executed"
    assert result["order"]["order_id"] == "o-1"
    assert ["paper", "buy", "BTCUSD", "0.05"] in calls


def test_buy_exceeding_balance_is_rejected_and_places_no_order():
    kraken_fn, calls = _stub_kraken()
    reason_fn = _stub_reason({"action": "buy", "size": "10", "rationale": "yolo"})
    graph = build_graph(reason_fn, run_kraken_fn=kraken_fn)

    result = graph.invoke(_initial_state())

    assert result["decision"] == "rejected"
    assert "insufficient balance" in result["reject_reason"]
    assert result["order"] is None
    assert not any(c[:2] == ["paper", "buy"] for c in calls)


def test_hold_is_rejected_and_places_no_order():
    kraken_fn, calls = _stub_kraken()
    reason_fn = _stub_reason({"action": "hold", "size": "0", "rationale": "no signal"})
    graph = build_graph(reason_fn, run_kraken_fn=kraken_fn)

    result = graph.invoke(_initial_state())

    assert result["decision"] == "rejected"
    assert result["reject_reason"] == "hold"
    assert result["order"] is None


def test_invoke_config_callbacks_and_metadata_reach_the_reason_node():
    # Langfuse tracing depends on this: runner.run_once passes a callback
    # handler via graph.invoke(..., config={...}); LangGraph only forwards it
    # to node functions declared with a second ``config`` parameter.
    kraken_fn, _ = _stub_kraken()
    seen = {}

    def reason_fn(state, config):
        seen["config"] = config
        return {"proposal": {"action": "hold", "size": "0", "rationale": "x"}}

    graph = build_graph(reason_fn, run_kraken_fn=kraken_fn)
    sentinel_handler = BaseCallbackHandler()
    graph.invoke(_initial_state(), config={"callbacks": [sentinel_handler],
                                              "metadata": {"run_id": 7}})

    assert seen["config"]["metadata"]["run_id"] == 7
    assert sentinel_handler in seen["config"]["callbacks"].handlers
