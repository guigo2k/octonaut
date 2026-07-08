"""Runtime entrypoint: wires config, logging, the DB, the OpenRouter LLM, the
tick scheduler and the FastAPI app together, then serves via uvicorn.

Import-safe by construction: importing this module (``import agent.main``,
pytest collection, etc.) never starts uvicorn, never starts the scheduler,
and never touches ``OPENROUTER_*``/``DATABASE_URL`` or the network. All of
that wiring lives inside ``main()``, which only executes under
``if __name__ == "__main__":`` -- i.e. when this module is run as the
container's entrypoint (``python -m agent.main``).
"""

from __future__ import annotations

import os

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from langchain_openai import ChatOpenAI

from agent.api import create_app
from agent.config import load_config
from agent.db import init_db, make_engine
from agent.graph import make_reason_fn
from agent.kraken import ensure_paper
from agent.observability import configure_logging
from agent.runner import run_once

# Strategy cadence isn't a config.yaml field (unlike a per-strategy cron in a
# richer agent) -- one fixed tick interval for the single configured strategy.
TICK_SECONDS = int(os.environ.get("AGENT_TICK_SECONDS", "300"))


def main() -> None:
    config = load_config(os.environ.get("AGENT_CONFIG", "/etc/agent/config.yaml"))
    configure_logging(config.logging.level, config.logging.format)

    engine = make_engine(os.environ["DATABASE_URL"])
    init_db(engine)
    # Eagerly, not lazily on the first tick -- APScheduler's IntervalTrigger
    # only fires after the first full interval elapses, so without this the
    # agent would be un-tradeable (and SIGTERM would have nothing to init
    # before checking) for up to AGENT_TICK_SECONDS after every restart.
    ensure_paper(config.strategy.balance)

    llm = ChatOpenAI(
        model=os.environ["OPENROUTER_MODEL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    reason_fn = make_reason_fn(llm)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: run_once(config, engine, reason_fn),
        IntervalTrigger(seconds=TICK_SECONDS),
    )

    # ``scheduler`` is started/stopped inside ``create_app``'s FastAPI
    # lifespan -- i.e. within uvicorn's running event loop. Starting it here
    # (before ``uvicorn.run``) would raise ``RuntimeError: no running event
    # loop`` from apscheduler's ``AsyncIOScheduler.start()``.
    app = create_app(engine, scheduler=scheduler)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
