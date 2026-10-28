"""Shared pytest fixtures.

Unit tests run on a throwaway SQLite file, so they need no Postgres and no network.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The `with` block runs the app's lifespan, which creates the DB engine.
    with TestClient(create_app(settings)) as test_client:
        yield test_client
