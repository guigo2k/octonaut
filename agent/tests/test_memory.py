from agent.db import session_scope
from agent.memory import embed, recall, write_memory


def test_embed_returns_384_dim_vector():
    vec = embed("Bought BTC after a strong breakout confirmation.")
    assert len(vec) == 384
    assert all(isinstance(x, float) for x in vec)


def test_embed_is_semantically_meaningful():
    # cosine similarity: closer text should score higher than unrelated text
    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb)

    base = embed("Bought Bitcoin after a strong upward breakout.")
    similar = embed("Purchased BTC following a bullish breakout signal.")
    unrelated = embed("The weather in Lisbon is sunny today.")
    assert cos(base, similar) > cos(base, unrelated)


def test_write_then_recall_orders_by_similarity(engine):
    with session_scope(engine) as s:
        write_memory(
            s, ticker="BTCUSD",
            rationale="Bought BTC after a strong bullish breakout with volume confirmation.",
            outcome="filled",
        )
        write_memory(
            s, ticker="BTCUSD",
            rationale="Sold half the position ahead of a scheduled macro announcement.",
            outcome="filled",
        )

    with session_scope(engine) as s:
        results = recall(s, ticker="BTCUSD", query="breakout with strong volume", top_k=2)
        assert len(results) == 2
        assert "breakout" in results[0].rationale


def test_recall_filters_by_ticker(engine):
    with session_scope(engine) as s:
        write_memory(s, ticker="BTCUSD", rationale="BTC grid level filled on the way up.",
                     outcome="filled")
        write_memory(s, ticker="ETHUSD", rationale="ETH grid level filled on the way up.",
                     outcome="filled")

    with session_scope(engine) as s:
        results = recall(s, ticker="ETHUSD", query="grid level filled", top_k=5)
        assert len(results) == 1
        assert results[0].ticker == "ETHUSD"
