"""Tests for settings loading."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_reads_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")
    assert Settings(_env_file=None).database_url == "sqlite:///from-env.db"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
