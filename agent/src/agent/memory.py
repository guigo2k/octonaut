import os
from functools import lru_cache

from fastembed import TextEmbedding

from agent.db import TradeMemory

_MODEL_NAME = "BAAI/bge-small-en-v1.5"  # 384-dim, CPU/ONNX, no network at inference time

# fastembed's own default cache_dir is /tmp -- pin an explicit, stable path so
# the Dockerfile's build-stage warm-up and the runtime container agree on
# where the model lives (verified live: without this, HF_HUB_OFFLINE=1 looks
# in /tmp/fastembed_cache regardless of where the warm-up actually wrote it).
_CACHE_DIR = os.environ.get("FASTEMBED_CACHE_DIR", os.path.expanduser("~/.fastembed_cache"))


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(model_name=_MODEL_NAME, cache_dir=_CACHE_DIR)


def embed(text: str) -> list[float]:
    return next(_model().embed([text])).tolist()


def write_memory(session, *, ticker: str, rationale: str, outcome: str | None) -> None:
    session.add(TradeMemory(ticker=ticker, rationale=rationale, outcome=outcome,
                             embedding=embed(rationale)))


def recall(session, *, ticker: str, query: str, top_k: int = 3) -> list[TradeMemory]:
    """Semantically recall past trade memory for ``ticker``, nearest first.

    The one place pgvector similarity search earns its keep -- skill selection
    (``agent.skills``) is deterministic, so it doesn't need this.
    """
    return (
        session.query(TradeMemory)
        .filter(TradeMemory.ticker == ticker)
        .order_by(TradeMemory.embedding.cosine_distance(embed(query)))
        .limit(top_k)
        .all()
    )
