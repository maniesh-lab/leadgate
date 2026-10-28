"""Shared pytest fixtures.

Unit tests run on a throwaway SQLite file, so they need no Postgres and no network.
Every test gets its own fresh file (pytest's `tmp_path`), so tests never share data.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401  (importing it registers every table on Base.metadata)
from app.config import Settings
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    """A SQLite engine with every table already created."""
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as session:
        yield session


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The `with` block runs the app's lifespan, which creates the DB engine.
    with TestClient(create_app(settings)) as test_client:
        yield test_client
