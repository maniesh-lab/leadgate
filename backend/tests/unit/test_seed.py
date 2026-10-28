"""Tests for the seed data and `python -m app.cli seed`."""

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.auth.security import verify_password
from app.cli import main
from app.config import Settings
from app.db.models import ScoringRubric, User, UserRole
from app.db.seed import GENERAL_B2B_RULES, WEB_AGENCY_RULES, seed


@pytest.mark.parametrize("rules", [GENERAL_B2B_RULES, WEB_AGENCY_RULES])
def test_each_rubric_can_reach_exactly_100(rules: list[dict]) -> None:
    assert sum(rule["points"] for rule in rules) == 100


def test_seed_creates_admin_and_rubrics(db: Session) -> None:
    result = seed(db, "boss@example.com", "s3cret-pass")

    assert result.admin_created
    assert result.rubrics_created == ["general-b2b v1", "web-agency v1"]
    admin = db.scalars(select(User)).one()
    assert admin.role == UserRole.ADMIN
    assert verify_password("s3cret-pass", admin.password_hash)
    rubric = db.scalars(select(ScoringRubric).where(ScoringRubric.name == "web-agency")).one()
    assert rubric.created_by == admin.id
    assert rubric.rules == WEB_AGENCY_RULES


def test_seed_twice_changes_nothing(db: Session) -> None:
    seed(db, "boss@example.com", "first")
    result = seed(db, "boss@example.com", "second")

    assert not result.admin_created
    assert result.rubrics_created == []
    assert db.scalar(select(func.count()).select_from(ScoringRubric)) == 2
    # The existing admin keeps the original password.
    assert verify_password("first", db.scalars(select(User)).one().password_hash)


def test_cli_seed(engine: Engine, settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    settings = settings.model_copy(
        update={"admin_email": "boss@example.com", "admin_password": "pw"}
    )
    assert main(["seed"], settings) == 0
    assert "admin boss@example.com: created" in capsys.readouterr().out


def test_cli_seed_needs_admin_settings(
    engine: Engine, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["seed"], settings) == 1
    assert "ADMIN_EMAIL and ADMIN_PASSWORD must be set" in capsys.readouterr().out
