import logging
import uuid
from decimal import Decimal

from agent.config import Config
from agent.db import Trade, session_scope
from agent.graph import build_graph
from agent.kraken import ensure_paper
from agent.kraken import run_kraken as default_run_kraken
from agent.memory import recall, write_memory
from agent.observability import current_trace_id, make_handler
from agent.skills import load_skills

logger = logging.getLogger(__name__)


def run_once(config: Config, engine, reason_fn, run_kraken_fn=default_run_kraken) -> dict:
    """One strategy tick: init paper account, recall memory, run the graph,
    then persist the ledger + trade memory if a trade actually executed.

    ``reason_fn`` is injected (see ``agent.graph.make_reason_fn`` for the real
    LLM-backed implementation) so this orchestration is testable without a
    real LLM or the ``kraken`` binary.
    """
    strategy = config.strategy
    ensure_paper(strategy.balance, run_kraken_fn)
    skills_text = load_skills(strategy.type)

    with session_scope(engine) as s:
        memories = [
            m.rationale
            for m in recall(s, ticker=strategy.ticker, query=strategy.prompt)
        ]

    run_id = uuid.uuid4().hex[:12]
    handler = make_handler(run_id)
    invoke_config = {"metadata": {"run_id": run_id}}
    if handler is not None:
        invoke_config["callbacks"] = [handler]

    graph = build_graph(reason_fn, run_kraken_fn=run_kraken_fn)
    result = graph.invoke({
        "ticker": strategy.ticker,
        "prompt": strategy.prompt,
        "skills_text": skills_text,
        "memory": memories,
    }, config=invoke_config)

    logger.info("strategy run finished", extra={
        "run_id": run_id,
        "decision": result["decision"],
        "langfuse_trace_id": current_trace_id(handler),
    })

    if result["decision"] == "executed" and result["order"] is not None:
        proposal = result["proposal"]
        with session_scope(engine) as s:
            s.add(Trade(
                side=proposal["action"],
                pair=strategy.ticker,
                size=Decimal(str(proposal["size"])),
                status="open" if result["order"]["ok"] else "failed",
                kraken_order_id=result["order"]["order_id"],
                rationale=proposal["rationale"],
            ))
            write_memory(s, ticker=strategy.ticker, rationale=proposal["rationale"],
                         outcome=result["decision"])

    return result
