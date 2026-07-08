import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from agent.db import Position, Trade, session_scope
from agent.kraken import close_all_open_orders
from agent.kraken import run_kraken as default_run_kraken

logger = logging.getLogger(__name__)


def _trade_to_dict(t: Trade) -> dict:
    return {
        "id": t.id,
        "side": t.side,
        "pair": t.pair,
        "size": str(t.size),
        "status": t.status,
        "kraken_order_id": t.kraken_order_id,
        "rationale": t.rationale,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _position_to_dict(p: Position) -> dict:
    return {
        "id": p.id,
        "taken_at": p.taken_at.isoformat() if p.taken_at else None,
        "payload": p.payload,
    }


def create_app(engine, *, scheduler=None, run_kraken_fn=default_run_kraken) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown()
            # SIGTERM contract: close every open paper order before the pod exits.
            cancelled = close_all_open_orders(run_kraken_fn)
            logger.info("cancelled all open orders before shutdown",
                        extra={"cancelled_order_ids": cancelled})

    app = FastAPI(title="octonaut agent", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/trades")
    def trades():
        with session_scope(engine) as s:
            rows = s.query(Trade).order_by(Trade.id).all()
            return [_trade_to_dict(t) for t in rows]

    @app.get("/positions")
    def positions():
        with session_scope(engine) as s:
            row = s.query(Position).order_by(Position.id.desc()).first()
            return [_position_to_dict(row)] if row is not None else []

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
