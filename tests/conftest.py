import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

load_dotenv()


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL is not set")
    return url


def _ensure_database_exists(url: str) -> None:
    """Create the test database if missing (never drops anything)."""
    parsed = make_url(url)
    admin = create_engine(
        parsed.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": parsed.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    admin.dispose()


def _migrate(url: str) -> None:
    os.environ["DATABASE_URL"] = url  # alembic/env.py reads this
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _test_database_url()
    if make_url(url).database == make_url(os.environ.get("DATABASE_URL", "")).database:
        raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")
    _ensure_database_exists(url)
    _migrate(url)
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Each test runs in a transaction that is rolled back afterwards."""
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield db
    db.close()
    transaction.rollback()
    connection.close()
