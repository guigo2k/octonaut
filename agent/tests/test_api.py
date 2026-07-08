from decimal import Decimal

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.db import Position, Trade, session_scope
from agent.kraken import KrakenResult


def test_health(engine):
    app = create_app(engine)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_trades_returns_seeded_rows(engine):
    with session_scope(engine) as s:
        s.add(Trade(side="buy", pair="BTCUSD", size=Decimal("0.01"), status="open",
                     kraken_order_id="o-1", rationale="breakout"))

    app = create_app(engine)
    with TestClient(app) as client:
        body = client.get("/trades").json()
        assert len(body) == 1
        assert body[0]["pair"] == "BTCUSD"
        assert body[0]["kraken_order_id"] == "o-1"


def test_positions_returns_latest_only(engine):
    with session_scope(engine) as s:
        s.add(Position(payload={"equity": "1"}))
        s.add(Position(payload={"equity": "2"}))

    app = create_app(engine)
    with TestClient(app) as client:
        body = client.get("/positions").json()
        assert len(body) == 1
        assert body[0]["payload"]["equity"] == "2"


def test_metrics_is_prometheus_text(engine):
    app = create_app(engine)
    with TestClient(app) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]


def test_lifespan_starts_and_stops_scheduler(engine):
    calls = []

    class FakeScheduler:
        def start(self):
            calls.append("start")

        def shutdown(self):
            calls.append("shutdown")

    app = create_app(engine, scheduler=FakeScheduler())
    assert calls == []
    with TestClient(app):
        assert calls == ["start"]
    assert calls == ["start", "shutdown"]


def test_lifespan_closes_open_orders_on_shutdown(engine):
    calls = []

    def fake_run_kraken(args):
        calls.append(args)
        if args == ["paper", "orders"]:
            return KrakenResult(True, {"count": 1, "mode": "paper", "open_orders": [{"id": "o-1"}]},
                                  None, 0, "paper orders")
        return KrakenResult(True, {"cancelled": True}, None, 0, "paper cancel")

    app = create_app(engine, run_kraken_fn=fake_run_kraken)
    with TestClient(app):
        pass
    assert ["paper", "cancel", "o-1"] in calls


def test_lifespan_logs_cancelled_orders_on_shutdown(engine, caplog):
    def fake_run_kraken(args):
        if args == ["paper", "orders"]:
            return KrakenResult(True, {"count": 1, "mode": "paper", "open_orders": [{"id": "o-9"}]},
                                  None, 0, "paper orders")
        return KrakenResult(True, {"cancelled": True}, None, 0, "paper cancel")

    app = create_app(engine, run_kraken_fn=fake_run_kraken)
    with caplog.at_level("INFO"):
        with TestClient(app):
            pass

    records = [r for r in caplog.records if "cancelled" in r.getMessage()]
    assert len(records) == 1
    assert records[0].cancelled_order_ids == ["o-9"]
