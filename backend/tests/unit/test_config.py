"""Tests for settings loading."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_reads_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")
    assert Settings(_env_file=None).database_url == "sqlite:///from-env.db"


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("OSM_USER_AGENT", "")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.osm_user_agent.startswith("leadgate/")
    assert settings.google_places_api_key is None


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
