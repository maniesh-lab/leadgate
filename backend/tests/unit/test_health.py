"""Tests for /api/health and /api/ready."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_JWT_SECRET


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_ok_when_db_reachable(client: TestClient) -> None:
    response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_ready_503_when_db_unreachable() -> None:
    # SQLite can't create a file inside a folder that doesn't exist, so connecting fails.
    settings = Settings(
        _env_file=None,
        database_url="sqlite:////no/such/folder/test.db",
        jwt_secret=TEST_JWT_SECRET,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "db": "unreachable"}


def test_app_refuses_to_start_without_a_jwt_secret(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(settings.model_copy(update={"jwt_secret": None}))
