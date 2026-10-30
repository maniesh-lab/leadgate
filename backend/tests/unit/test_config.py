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
    monkeypatch.setenv("ENRICH_CACHE_DAYS", "")
    monkeypatch.setenv("ENRICH_MAX_PAGES", "")
    settings = Settings(_env_file=None)
    assert settings.osm_user_agent.startswith("leadgate/")
    assert settings.google_places_api_key is None
    assert (settings.enrich_cache_days, settings.enrich_max_pages) == (14, 4)


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_errors_never_show_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD", "super-secret-value")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "super-secret-value" not in str(error.value)


def test_secrets_are_not_in_the_printed_settings() -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        admin_password="pw-1",
        google_places_api_key="k-1",
        jwt_secret="j" * 32,
    )
    assert "pw-1" not in repr(settings) and "k-1" not in repr(settings)
    assert "j" * 32 not in repr(settings)


def test_jwt_secret_must_be_long() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite://", jwt_secret="short")
