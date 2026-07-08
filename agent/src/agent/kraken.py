import json
import shutil
import subprocess
from dataclasses import dataclass

from langchain_core.tools import tool


@dataclass(frozen=True)
class KrakenResult:
    ok: bool
    data: object | None
    error: str | None
    exit_code: int
    command: str


def normalize_pair(pair: str) -> str:
    """Config pairs like ``BTC/USD`` -> the CLI's ``BTCUSD`` form."""
    return pair.replace("/", "").upper()


def run_kraken(args: list[str], timeout: int = 30) -> KrakenResult:
    """Run ``kraken <args> -o json`` and return a structured result."""
    binary = shutil.which("kraken") or "kraken"
    cmd = [binary, *args, "-o", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    printable = " ".join(cmd)
    if proc.returncode != 0:
        return KrakenResult(False, None, proc.stderr.strip() or proc.stdout.strip(),
                             proc.returncode, printable)
    try:
        return KrakenResult(True, json.loads(proc.stdout), None, 0, printable)
    except json.JSONDecodeError:
        return KrakenResult(False, None, f"non-json output: {proc.stdout[:200]}", 0, printable)


def ensure_paper(balance, run_kraken_fn=None) -> None:
    """Idempotently initialize the paper account with the configured starting balance."""
    fn = run_kraken_fn or run_kraken
    status = fn(["paper", "status"])
    initialized = status.ok and isinstance(status.data, dict) and "current_value" in status.data
    if not initialized:
        fn(["paper", "init", "--balance", str(balance)])


def close_all_open_orders(run_kraken_fn=None) -> list[str]:
    """Cancel every open paper order. Returns the ids cancelled.

    Called from the SIGTERM shutdown path -- best-effort: an empty list means
    either there were no open orders, or ``paper orders`` itself failed (in
    which case there's nothing safe left to cancel by id).

    Real shape (verified live, kraken-cli v0.3.2): ``{"count": N, "mode":
    "paper", "open_orders": [{"id": ..., ...}, ...]}`` -- a dict, not a bare
    list.
    """
    fn = run_kraken_fn or run_kraken
    open_orders = fn(["paper", "orders"])
    orders = open_orders.data.get("open_orders") if isinstance(open_orders.data, dict) else None
    if not open_orders.ok or not isinstance(orders, list):
        return []
    cancelled = []
    for order in orders:
        order_id = order.get("id") or order.get("order_id")
        if order_id is None:
            continue
        fn(["paper", "cancel", order_id])
        cancelled.append(order_id)
    return cancelled


@tool
def get_ticker(pair: str) -> dict:
    """Get the latest ticker for a pair like BTC/USD (public market data)."""
    r = run_kraken(["ticker", normalize_pair(pair)])
    return {"ok": r.ok, "data": r.data, "error": r.error}


@tool
def get_ohlc(pair: str, interval: int = 60) -> dict:
    """Get OHLC candles for a pair; interval in minutes (public market data)."""
    r = run_kraken(["ohlc", normalize_pair(pair), "--interval", str(interval)])
    return {"ok": r.ok, "data": r.data, "error": r.error}


@tool
def get_balance() -> dict:
    """Get paper account balances (per-asset available/reserved/total)."""
    r = run_kraken(["paper", "balance"])
    return {"ok": r.ok, "data": r.data, "error": r.error}


@tool
def get_status() -> dict:
    """Get paper account status: current_value (equity), P&L, open orders, fees."""
    r = run_kraken(["paper", "status"])
    return {"ok": r.ok, "data": r.data, "error": r.error}


@tool
def place_paper_order(pair: str, side: str, volume: str) -> dict:
    """Place a PAPER market order (side = "buy" or "sell").

    Bound only to the deterministic execution step -- never to the LLM
    reasoning node, so the model can propose a trade but cannot itself place one.
    """
    r = run_kraken(["paper", side, normalize_pair(pair), volume])
    return {"ok": r.ok, "data": r.data, "error": r.error, "command": r.command,
            "exit_code": r.exit_code}


def read_only_tools():
    return [get_ticker, get_ohlc, get_balance, get_status]


def execution_tools():
    return [place_paper_order]
