from contextlib import contextmanager
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Column, DateTime, Integer, Numeric, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Trade(Base):
    """The ledger: one row per placed paper order."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    side = Column(String, nullable=False)
    pair = Column(String, nullable=False)
    size = Column(Numeric, nullable=False)
    status = Column(String, nullable=False)
    kraken_order_id = Column(String)
    rationale = Column(String)
    created_at = Column(DateTime(timezone=True), default=_now)


class Position(Base):
    """Latest account snapshot pulled from ``kraken paper status``/``balance``."""

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True)
    taken_at = Column(DateTime(timezone=True), default=_now)
    payload = Column(JSON, nullable=False)


class TradeMemory(Base):
    """Embedded trade rationale/outcome, recalled by similarity before each run."""

    __tablename__ = "trade_memory"

    id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False)
    rationale = Column(String, nullable=False)
    outcome = Column(String)
    embedding = Column(Vector(384), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)


def make_engine(url: str):
    return create_engine(url)


def init_db(engine) -> None:
    """Idempotent DDL: create the ``vector`` extension, then any missing tables.

    No Alembic here -- one small schema, ``CREATE ... IF NOT EXISTS`` throughout
    is enough at this scope (no migration history/rollback; see README).
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


_Session = sessionmaker()


@contextmanager
def session_scope(engine):
    session = _Session(bind=engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
