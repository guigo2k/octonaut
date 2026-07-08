import os

import pytest
from sqlalchemy import text

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:pw@localhost:55433/postgres"
)


@pytest.fixture
def engine():
    from agent.db import Base, init_db, make_engine

    try:
        eng = make_engine(TEST_DATABASE_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - environment probe, not app logic
        pytest.skip(f"no reachable test postgres at {TEST_DATABASE_URL}: {exc}")
    init_db(eng)
    yield eng
    with eng.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    eng.dispose()
