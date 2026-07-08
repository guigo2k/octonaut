import json
import subprocess

from agent.kraken import (
    KrakenResult,
    close_all_open_orders,
    ensure_paper,
    execution_tools,
    get_ticker,
    normalize_pair,
    read_only_tools,
    run_kraken,
)


def test_normalize_pair_strips_slash_and_uppercases():
    assert normalize_pair("btc/usd") == "BTCUSD"
    assert normalize_pair("BTCUSD") == "BTCUSD"


def test_parses_json_success(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"result": {"BTCUSD": {"c": ["60000"]}}}), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = run_kraken(["ticker", "BTCUSD"])
    assert r.ok and r.data["result"]["BTCUSD"]["c"][0] == "60000"
    assert "-o json" in r.command


def test_nonzero_exit_is_error(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", json.dumps({"error": "bad pair"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = run_kraken(["ticker", "NOPE"])
    assert not r.ok and r.exit_code == 1


def test_non_json_stdout_is_error(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "not json", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = run_kraken(["ticker", "BTCUSD"])
    assert not r.ok and "non-json" in r.error


def test_ensure_paper_inits_with_balance_when_not_initialized():
    calls = []

    def fn(args):
        calls.append(args)
        if args == ["paper", "status"]:
            return KrakenResult(False, None, "not initialized", 1, "paper status")
        return KrakenResult(True, {"ok": True}, None, 0, "paper init")

    ensure_paper(50000, fn)
    assert ["paper", "init", "--balance", "50000"] in calls


def test_ensure_paper_skips_when_already_initialized():
    calls = []

    def fn(args):
        calls.append(args)
        return KrakenResult(True, {"current_value": 10000}, None, 0, "paper status")

    ensure_paper(50000, fn)
    assert all(a[:2] != ["paper", "init"] for a in calls)


def test_close_all_open_orders_cancels_each_one():
    # Real shape (verified live against kraken-cli v0.3.2's `paper orders`):
    # {"count": N, "mode": "paper", "open_orders": [{"id": ..., ...}, ...]}
    calls = []

    def fn(args):
        calls.append(args)
        if args == ["paper", "orders"]:
            return KrakenResult(
                True, {"count": 2, "mode": "paper",
                        "open_orders": [{"id": "o1"}, {"id": "o2"}]},
                None, 0, "paper orders",
            )
        return KrakenResult(True, {"cancelled": True}, None, 0, "paper cancel")

    cancelled = close_all_open_orders(fn)
    assert cancelled == ["o1", "o2"]
    assert ["paper", "cancel", "o1"] in calls
    assert ["paper", "cancel", "o2"] in calls


def test_close_all_open_orders_handles_zero_open_orders():
    def fn(args):
        if args == ["paper", "orders"]:
            return KrakenResult(True, {"count": 0, "mode": "paper", "open_orders": []},
                                  None, 0, "paper orders")
        raise AssertionError("cancel should not be called with no open orders")

    assert close_all_open_orders(fn) == []


def test_close_all_open_orders_returns_empty_on_failure():
    def fn(args):
        return KrakenResult(False, None, "boom", 1, "paper orders")

    assert close_all_open_orders(fn) == []


def test_read_only_excludes_order_placement():
    names = {t.name for t in read_only_tools()}
    assert "get_ticker" in names
    assert "place_paper_order" not in names  # safety invariant


def test_execution_tools_have_order_placement():
    names = {t.name for t in execution_tools()}
    assert "place_paper_order" in names


def test_get_ticker_tool_normalizes_pair_and_calls_run_kraken(monkeypatch):
    seen = {}

    def fake_run_kraken(args, timeout=30):
        seen["args"] = args
        return KrakenResult(True, {"c": ["1"]}, None, 0, "ticker")

    monkeypatch.setattr("agent.kraken.run_kraken", fake_run_kraken)
    result = get_ticker.func("btc/usd")
    assert seen["args"] == ["ticker", "BTCUSD"]
    assert result["ok"] is True
