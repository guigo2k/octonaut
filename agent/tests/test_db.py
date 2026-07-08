from decimal import Decimal

from agent.db import Position, Trade, TradeMemory, init_db, session_scope


def test_init_db_is_idempotent(engine):
    init_db(engine)
    init_db(engine)


def test_trade_roundtrip(engine):
    with session_scope(engine) as s:
        s.add(Trade(side="buy", pair="BTCUSD", size=Decimal("0.01"),
                     status="open", kraken_order_id="abc123", rationale="grid level hit"))

    with session_scope(engine) as s:
        rows = s.query(Trade).all()
        assert len(rows) == 1
        assert rows[0].side == "buy"
        assert rows[0].pair == "BTCUSD"
        assert rows[0].size == Decimal("0.01")
        assert rows[0].kraken_order_id == "abc123"
        assert rows[0].created_at is not None


def test_position_roundtrip(engine):
    with session_scope(engine) as s:
        s.add(Position(payload={"equity": "50000", "open_orders": 2}))

    with session_scope(engine) as s:
        rows = s.query(Position).all()
        assert len(rows) == 1
        assert rows[0].payload["equity"] == "50000"


def test_trade_memory_vector_similarity_orders_by_distance(engine):
    near = [1.0] + [0.0] * 383
    far = [0.0] * 383 + [1.0]
    query = [0.9] + [0.0] * 383

    with session_scope(engine) as s:
        s.add(TradeMemory(ticker="BTCUSD", rationale="near", outcome="filled", embedding=near))
        s.add(TradeMemory(ticker="BTCUSD", rationale="far", outcome="filled", embedding=far))

    with session_scope(engine) as s:
        ranked = (
            s.query(TradeMemory)
            .order_by(TradeMemory.embedding.cosine_distance(query))
            .all()
        )
        assert [r.rationale for r in ranked] == ["near", "far"]
