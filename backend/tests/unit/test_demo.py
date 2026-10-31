"""Tests for the public demo's data (`app.db.demo`, `python -m app.cli demo`). The demo
reviewer's login rules are tested in test_api_auth.py."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.auth.security import verify_password
from app.cli import main
from app.config import Settings
from app.db.demo import CAMPAIGN_NAME, SITES, set_up_demo
from app.db.models import Campaign, Lead, Run, RunItem, RunItemStatus, User, UserRole
from app.db.seed import seed
from tests.agent_world import World
from tests.conftest import add_user
from tests.unit.conftest import run_jobs

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password"


def demo(db: Session) -> object:
    return set_up_demo(db, DEMO_EMAIL, DEMO_PASSWORD, datetime.now(UTC))


def waiting_items(db: Session) -> list[RunItem]:
    return list(
        db.scalars(
            select(RunItem)
            .where(RunItem.status == RunItemStatus.AWAITING_APPROVAL)
            .order_by(RunItem.id)
        )
    )


def test_set_up_makes_the_reviewer_and_campaign_and_queues_a_run(world: World) -> None:
    result = demo(world.db)
    assert result.reviewer == "created"
    assert result.waiting == 0
    assert result.run_id is not None

    user = world.db.scalars(select(User).where(User.email == DEMO_EMAIL)).one()
    assert user.role == UserRole.REVIEWER
    assert verify_password(DEMO_PASSWORD, user.password_hash)
    campaign = world.db.get(Campaign, result.campaign_id)
    assert campaign.name == CAMPAIGN_NAME
    assert campaign.sources == ["csv"]

    # Running it again while the run is still queued changes nothing.
    again = demo(world.db)
    assert (again.reviewer, again.campaign_id, again.run_id) == ("unchanged", campaign.id, None)


def test_the_worker_takes_the_demo_leads_to_the_gate_without_crawling(
    world: World, worker, clock
) -> None:
    demo(world.db)
    assert run_jobs(worker, clock) == 13  # start_run + 12 process_item

    items = waiting_items(world.db)
    assert len(items) == 12
    flags = {world.db.get(Lead, item.lead_id).name: item.flags for item in items}
    assert any(
        flag.startswith("possible_duplicate") for flag in flags["Barton Creek Family Dental South"]
    )
    assert "category_mismatch" in flags["Allandale Pharmacy"]
    assert "closed" in flags["Old Town Dental Clinic"]
    # The sites don't exist: the stored findings were used, nothing was fetched.
    assert world.web.requests == []
    lead = world.db.scalars(select(Lead).where(Lead.name == "Hyde Park Smiles")).one()
    assert lead.enrichment["signals"] == SITES["https://hydeparksmiles.example"]["signals"]
    hot = world.db.scalars(select(Lead).where(Lead.name == "Barton Creek Family Dental")).one()
    assert hot.score is not None and hot.score >= 60  # old http site, great reviews


def test_the_queue_is_refilled_only_when_it_runs_low(world: World, worker, clock) -> None:
    demo(world.db)
    run_jobs(worker, clock)

    assert demo(world.db).run_id is None  # 12 waiting

    for item in waiting_items(world.db)[:8]:
        item.status = RunItemStatus.DONE
    world.db.commit()
    refill = demo(world.db)
    assert refill.waiting == 4
    assert refill.run_id is not None
    run_jobs(worker, clock)
    assert len(waiting_items(world.db)) == 16
    assert world.db.scalar(select(func.count()).select_from(Run)) == 3  # world's own + 2


def test_a_changed_password_is_set_back(world: World) -> None:
    add_user(world.db, DEMO_EMAIL, UserRole.REVIEWER, password="someone-changed-it")
    assert demo(world.db).reviewer == "password reset"
    user = world.db.scalars(select(User).where(User.email == DEMO_EMAIL)).one()
    assert verify_password(DEMO_PASSWORD, user.password_hash)


def test_an_admin_email_is_never_used_as_the_demo_login(world: World) -> None:
    add_user(world.db, DEMO_EMAIL, UserRole.ADMIN)
    with pytest.raises(ValueError, match="not a reviewer"):
        demo(world.db)


def test_cli_demo_with_history(
    engine: Engine, db: Session, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(db, "admin@example.com", "admin-password")
    settings.demo_reviewer_email = DEMO_EMAIL
    settings.demo_reviewer_password = DEMO_PASSWORD

    assert main(["demo", "--history", "40"], settings) == 0
    out = capsys.readouterr().out
    assert "history: 40 fake leads" in out
    assert f"demo reviewer {DEMO_EMAIL}: created" in out
    assert "queued" in out
    # The fake history leaves nothing at the gate: the queue only gets real items.
    assert waiting_items(db) == []

    assert main(["demo", "--history", "40"], settings) == 0
    out = capsys.readouterr().out
    assert "fake leads already there, skipped" in out
    assert "no new run needed" in out


def test_cli_demo_needs_the_demo_login(settings: Settings, capsys) -> None:
    assert main(["demo"], settings) == 1
    assert "DEMO_REVIEWER_EMAIL" in capsys.readouterr().out


def test_cli_demo_needs_the_seeded_rubrics(engine: Engine, settings: Settings, capsys) -> None:
    settings.demo_reviewer_email = DEMO_EMAIL
    settings.demo_reviewer_password = DEMO_PASSWORD
    assert main(["demo"], settings) == 1
    assert "run `python -m app.cli seed` first" in capsys.readouterr().out


def test_the_worker_refills_the_demo_queue_every_minute(
    world: World, worker, clock, settings: Settings
) -> None:
    demo(world.db)
    run_jobs(worker, clock)
    for item in waiting_items(world.db):
        item.status = RunItemStatus.DONE
    world.db.commit()

    worker.recover_now_and_then()  # no demo login set: nothing to refill
    assert run_jobs(worker, clock) == 0

    settings.demo_reviewer_email = DEMO_EMAIL
    clock.advance(61)
    worker.recover_now_and_then()
    assert run_jobs(worker, clock) == 13
    assert len(waiting_items(world.db)) == 12
