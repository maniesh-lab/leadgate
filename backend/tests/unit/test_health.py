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


def test_cors_is_off_by_default(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "https://elsewhere.test"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_only_the_listed_origins(settings: Settings) -> None:
    settings = settings.model_copy(
        update={"cors_origins": "https://app.leadgate.test, https://other.test"}
    )
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }
    with TestClient(create_app(settings)) as client:
        allowed = client.options(
            "/api/auth/me", headers={"Origin": "https://app.leadgate.test", **preflight}
        )
        refused = client.options(
            "/api/auth/me", headers={"Origin": "https://evil.test", **preflight}
        )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.leadgate.test"
    assert "access-control-allow-origin" not in refused.headers
