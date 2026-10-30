"""Tests for `python -m app.cli start-run`, `decide` and `show-run` (no worker runs here)."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import main
from app.config import Settings
from app.db.models import (
    Approval,
    Job,
    JobType,
    Run,
    RunItem,
    RunItemStatus,
    RunStep,
    StepStatus,
    User,
    UserRole,
)
from tests.agent_world import World, add_lead


@pytest.fixture
def world(db: Session) -> World:
    return World(db)


def waiting_item(world: World) -> RunItem:
    item = world.add_item(add_lead(world.db))
    item.status = RunItemStatus.AWAITING_APPROVAL
    world.db.add(User(email="admin@example.com", password_hash="x", role=UserRole.ADMIN))
    world.db.commit()
    return item


def test_start_run_queues_a_run(world: World, settings: Settings, capsys) -> None:
    assert main(["start-run", "--campaign", str(world.campaign.id)], settings) == 0

    world.db.expire_all()
    run = world.db.scalars(select(Run).order_by(Run.id.desc())).first()
    assert run.status == "queued"
    assert world.db.scalars(select(Job)).one().type == JobType.START_RUN
    assert f"run {run.id} queued" in capsys.readouterr().out
    assert main(["start-run", "--campaign", "999"], settings) == 1


def test_decide_saves_the_decision(world: World, settings: Settings, capsys) -> None:
    item = waiting_item(world)
    args = ["decide", "--item", str(item.id), "--decision", "approved"]

    assert main([*args, "--reviewer", "admin@example.com"], settings) == 0
    assert "approved by admin@example.com" in capsys.readouterr().out
    world.db.expire_all()
    assert world.db.scalars(select(Approval)).one().decision == "approved"

    # A second decision, an unknown reviewer, no reviewer at all.
    assert main([*args, "--reviewer", "admin@example.com"], settings) == 1
    assert "is resuming" in capsys.readouterr().out
    assert main([*args, "--reviewer", "nobody@example.com"], settings) == 1
    assert main(args, settings) == 1
    assert "--reviewer" in capsys.readouterr().out


def test_decide_uses_admin_email_by_default(world: World, settings: Settings) -> None:
    item = waiting_item(world)
    settings = settings.model_copy(update={"admin_email": "admin@example.com"})

    args = ["decide", "--item", str(item.id), "--decision", "rejected", "--reason", "closed"]
    assert main(args, settings) == 0


def test_show_run_lists_items_and_double_steps(world: World, settings: Settings, capsys) -> None:
    item = waiting_item(world)
    item.score, item.flags = 60, ["closed"]
    world.db.commit()

    assert main(["show-run", "--run", str(world.run.id)], settings) == 0
    out = capsys.readouterr().out
    assert f"run {world.run.id}: queued" in out
    assert f"item {item.id} Smile Austin Dental: awaiting_approval, score 60" in out
    assert "no node completed twice" in out

    for _ in range(2):
        world.db.add(RunStep(run_item_id=item.id, node="score", status=StepStatus.COMPLETED))
    world.db.commit()
    main(["show-run", "--run", str(world.run.id)], settings)
    assert f"!! item {item.id}: score completed 2 times" in capsys.readouterr().out
    assert main(["show-run", "--run", "999"], settings) == 1
