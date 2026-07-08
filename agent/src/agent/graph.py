"""The strategy-run graph: gather market/account state -> reason (LLM, with
read-only market-data tools) -> deterministic solvency guard -> deterministic
execution.

Safety invariant: order placement (``execute_node``) is driven only by the
deterministic ``guard_node``'s decision -- the reasoning LLM can propose a
trade but never places one itself; ``get_ticker``/``get_ohlc`` are the only
tools bound to it.

The kraken JSON shapes ``_price_from_ticker``/``_equity_from_status``/
``_holding_size`` parse are real (verified against the live ``kraken`` CLI by
a prior build of this same wrapper -- ticker keyed by the normalized pair with
a ``c: [last, lotvol]`` field, ``paper status``'s ``current_value``, ``paper
balance``'s ``{"balances": {ASSET: {"total": ...}}}``); the base-asset-from-
ticker split (stripping a known quote suffix) is a simplification for
single-quote-currency pairs like ``BTCUSD``, re-verified at live integration
time.
"""

from decimal import Decimal
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from agent.kraken import get_ohlc, get_ticker
from agent.kraken import run_kraken as default_run_kraken

_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "EUR")


class Proposal(BaseModel):
    action: Literal["buy", "sell", "hold"]
    size: Decimal = Decimal("0")
    rationale: str


class RunState(TypedDict, total=False):
    ticker: str
    prompt: str
    skills_text: str
    memory: list[str]
    market: dict | None
    account: dict | None
    proposal: dict | None
    decision: str | None
    reject_reason: str | None
    order: dict | None


def _price_from_ticker(ticker: object) -> Decimal:
    if isinstance(ticker, dict):
        for key in ("last", "price", "close"):
            if ticker.get(key) is not None:
                return Decimal(str(ticker[key]))
        c = ticker.get("c")
        if isinstance(c, (list, tuple)) and c:
            return Decimal(str(c[0]))
        for value in ticker.values():
            if isinstance(value, dict):
                inner = value.get("c")
                if isinstance(inner, (list, tuple)) and inner:
                    return Decimal(str(inner[0]))
    raise ValueError(f"cannot extract price from ticker payload: {ticker!r}")


def _equity_from_status(status: object) -> Decimal:
    if isinstance(status, dict):
        for key in ("current_value", "equity", "total"):
            if status.get(key) is not None:
                return Decimal(str(status[key]))
    raise ValueError(f"cannot extract equity from status payload: {status!r}")


def _base_asset(ticker: str) -> str:
    for quote in _QUOTE_SUFFIXES:
        if ticker.endswith(quote):
            return ticker[: -len(quote)]
    return ticker


def _holding_size(balance: object, ticker: str) -> Decimal:
    base = _base_asset(ticker)
    balances = balance.get("balances", balance) if isinstance(balance, dict) else {}
    info = balances.get(base) if isinstance(balances, dict) else None
    total = info.get("total") if isinstance(info, dict) else info
    return Decimal(str(total)) if total is not None else Decimal("0")


def solvency_guard(
    proposal: Proposal, *, balance: Decimal, price: Decimal, held: Decimal
) -> tuple[bool, str | None]:
    """Pure, deterministic, no LLM: the sole gate before an order is placed."""
    if proposal.action == "hold":
        return False, "hold"
    if proposal.action == "buy":
        cost = proposal.size * price
        if cost > balance:
            return False, f"insufficient balance: cost {cost} > balance {balance}"
        return True, None
    if proposal.action == "sell":
        if proposal.size > held:
            return False, f"insufficient position: size {proposal.size} > held {held}"
        return True, None
    return False, "unknown action"


def make_reason_fn(llm):
    """Real reasoning step: a ReAct agent bound to read-only market-data tools,
    returning a structured ``Proposal`` as its final answer.
    """
    react_agent = create_react_agent(model=llm, tools=[get_ticker, get_ohlc],
                                      response_format=Proposal)

    def reason_fn(state: RunState, config: dict | None = None) -> dict:
        # ``config`` (callbacks/metadata) is propagated here by LangGraph only
        # because this node function declares it as a second parameter --
        # required for the Langfuse callback handler runner.run_once attaches
        # to actually trace this node's LLM + tool calls.
        system = SystemMessage(content=(
            f"{state['skills_text']}\n\n{state['prompt']}\n\n"
            "Decide whether to buy, sell, or hold. Use the market-data tools "
            "as needed, then give a final structured decision."
        ))
        memory_note = "\n".join(state["memory"]) or "(no prior trade memory)"
        human = HumanMessage(content=f"Recalled trade memory:\n{memory_note}")
        result = react_agent.invoke({"messages": [system, human]}, config=config)
        proposal: Proposal = result["structured_response"]
        return {"proposal": proposal.model_dump(mode="json")}

    return reason_fn


def _gather_node(run_kraken_fn):
    def node(state: RunState) -> dict:
        ticker = run_kraken_fn(["ticker", state["ticker"]]).data
        status = run_kraken_fn(["paper", "status"]).data
        balance = run_kraken_fn(["paper", "balance"]).data
        return {
            "market": {"ticker": ticker},
            "account": {"status": status, "balance": balance},
            # Defaults so every terminal state (including the guard->END
            # rejection path, which never reaches ``execute``) has these keys.
            "decision": None,
            "reject_reason": None,
            "order": None,
        }

    return node


def _guard_node(state: RunState) -> dict:
    proposal = Proposal.model_validate(state["proposal"])
    price = _price_from_ticker(state["market"]["ticker"])
    balance = _equity_from_status(state["account"]["status"])
    held = _holding_size(state["account"]["balance"], state["ticker"])
    ok, reason = solvency_guard(proposal, balance=balance, price=price, held=held)
    return {"decision": "executed" if ok else "rejected", "reject_reason": reason}


def _execute_node(run_kraken_fn):
    def node(state: RunState) -> dict:
        if state["decision"] != "executed":
            return {"order": None}
        proposal = Proposal.model_validate(state["proposal"])
        r = run_kraken_fn(["paper", proposal.action, state["ticker"], str(proposal.size)])
        order_id = (r.data or {}).get("order_id") if r.ok else None
        return {"order": {"ok": r.ok, "order_id": order_id, "command": r.command}}

    return node


def build_graph(reason_fn, run_kraken_fn=default_run_kraken):
    g = StateGraph(RunState)
    g.add_node("gather", _gather_node(run_kraken_fn))
    g.add_node("reason", reason_fn)
    g.add_node("guard", _guard_node)
    g.add_node("execute", _execute_node(run_kraken_fn))
    g.set_entry_point("gather")
    g.add_edge("gather", "reason")
    g.add_edge("reason", "guard")
    g.add_conditional_edges(
        "guard", lambda s: "execute" if s["decision"] == "executed" else END,
        {"execute": "execute", END: END},
    )
    g.add_edge("execute", END)
    return g.compile()
