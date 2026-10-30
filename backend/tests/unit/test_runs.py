"""Tests for `app.services.runs` and `app.services.approvals` on SQLite (no graph here).

The campaign uses the messy CSV fixture as its only source, so nothing touches the network.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Approval,
    AuditLog,
    Campaign,
    Job,
    JobType,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    ScoringRubric,
    User,
    UserRole,
)
from app.services.approvals import NotAwaitingDecision, decide
from app.services.runs import collect_run, create_run, refresh_run_status
from app.sources.base import SourceError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
MESSY_CSV = str(FIXTURES / "messy_dentists.csv")
MAPPING = json.loads((FIXTURES / "messy_dentists_mapping.json").read_text())
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def campaign(db: Session) -> Campaign:
    rubric = ScoringRubric(name="general-b2b", version=1, rules=[])
    db.add(rubric)
    db.flush()
    campaign = Campaign(
        name="CSV dentists",
        niche="dentist",
        location="Austin, TX",
        sources=["csv"],
        source_options={"csv_path": MESSY_CSV, "csv_mapping": MAPPING},
        rubric_id=rubric.id,
    )
    db.add(campaign)
    db.commit()
    return campaign


@pytest.fixture
def no_network() -> httpx2.Client:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    return httpx2.Client(transport=httpx2.MockTransport(refuse))


def reviewer(db: Session) -> User:
    user = User(email="rev@example.com", password_hash="x", role=UserRole.REVIEWER)
    db.add(user)
    db.commit()
    return user


def actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_create_run_adds_the_run_and_its_start_job_together(db: Session, campaign) -> None:
    run = create_run(db, campaign, NOW, correlation_id="corr-1")

    job = db.scalars(select(Job)).one()
    assert (run.status, run.rubric_id, run.correlation_id) == (
        RunStatus.QUEUED,
        campaign.rubric_id,
        "corr-1",
    )
    assert job.type == JobType.START_RUN
    assert job.payload == {"run_id": run.id, "correlation_id": "corr-1"}
    assert actions(db) == ["run.created"]


def test_collect_run_makes_one_item_and_one_job_per_lead(
    db: Session, campaign, settings: Settings, no_network
) -> None:
    run = create_run(db, campaign, NOW)

    collect_run(db, run, settings, no_network, NOW)

    items = list(db.scalars(select(RunItem).order_by(RunItem.id)))
    jobs = list(db.scalars(select(Job).where(Job.type == JobType.PROCESS_ITEM)))
    assert len(items) == 6  # the fixture's 6 distinct leads
    assert {item.status for item in items} == {RunItemStatus.PENDING}
    assert items[0].thread_id == f"run-{run.id}-lead-{items[0].lead_id}"
    assert sorted(job.payload["run_item_id"] for job in jobs) == [item.id for item in items]
    assert run.status == RunStatus.RUNNING
    assert run.started_at == NOW
    assert run.stats["collected"] == 7
    assert run.stats["duplicates"] == 1
    assert run.stats["run_items"] == 6
    assert actions(db).count("lead.collected") == 6
    assert actions(db)[:2] == ["run.created", "run.started"]


def test_collecting_again_after_a_crash_adds_no_second_set_of_items(
    db: Session, campaign, settings: Settings, no_network
) -> None:
    run = create_run(db, campaign, NOW)
    collect_run(db, run, settings, no_network, NOW)

    collect_run(db, run, settings, no_network, NOW)

    assert len(list(db.scalars(select(RunItem)))) == 6


def test_a_run_whose_every_source_fails_raises_for_a_retry(
    db: Session, campaign, settings: Settings, no_network
) -> None:
    campaign.source_options = {}  # the csv source needs csv_path
    db.commit()
    run = create_run(db, campaign, NOW)

    with pytest.raises(SourceError, match="every source failed"):
        collect_run(db, run, settings, no_network, NOW)


def test_a_run_with_no_leads_is_completed_at_once(
    db: Session, campaign, settings: Settings, no_network
) -> None:
    campaign.sources = []
    db.commit()
    run = create_run(db, campaign, NOW)

    collect_run(db, run, settings, no_network, NOW)

    assert run.status == RunStatus.COMPLETED
    assert run.finished_at == NOW
    assert actions(db)[-1] == "run.completed"


def collected_run(db: Session, campaign, settings, no_network) -> Run:
    run = create_run(db, campaign, NOW)
    collect_run(db, run, settings, no_network, NOW)
    return run


def set_items(db: Session, run: Run, *statuses: RunItemStatus) -> list[RunItem]:
    items = list(db.scalars(select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.id)))
    for item, status in zip(items, statuses, strict=False):
        item.status = status
    return items


def test_run_status_follows_its_items(db: Session, campaign, settings, no_network) -> None:
    run = collected_run(db, campaign, settings, no_network)
    waiting = RunItemStatus.AWAITING_APPROVAL

    set_items(db, run, *[waiting] * 5, RunItemStatus.RUNNING)
    refresh_run_status(db, run, NOW)
    assert run.status == RunStatus.RUNNING

    set_items(db, run, *[waiting] * 6)
    refresh_run_status(db, run, NOW)
    assert run.status == RunStatus.WAITING_APPROVAL

    set_items(db, run, *[RunItemStatus.DONE] * 5, RunItemStatus.FAILED)
    refresh_run_status(db, run, NOW)
    assert run.status == RunStatus.COMPLETED
    assert run.stats["items"] == {"done": 5, "failed": 1}


def test_decide_saves_the_approval_and_queues_the_resume(
    db: Session, campaign, settings, no_network
) -> None:
    run = collected_run(db, campaign, settings, no_network)
    item = set_items(db, run, *[RunItemStatus.AWAITING_APPROVAL] * 6)[0]
    db.commit()
    user = reviewer(db)

    decide(db, item.id, user, "rejected", "  closed down  ", NOW)

    approval = db.scalars(select(Approval)).one()
    assert (approval.decision, approval.reason, approval.reviewer_id) == (
        "rejected",
        "closed down",
        user.id,
    )
    assert item.status == RunItemStatus.RESUMING
    resume = db.scalars(select(Job).where(Job.type == JobType.RESUME_ITEM)).one()
    assert resume.payload["run_item_id"] == item.id
    audit = db.scalars(select(AuditLog).where(AuditLog.action == "approval.rejected")).one()
    assert (audit.actor, audit.run_item_id) == ("rev@example.com", item.id)
    assert run.status == RunStatus.RUNNING  # an item is resuming


def test_a_second_decision_is_refused(db: Session, campaign, settings, no_network) -> None:
    run = collected_run(db, campaign, settings, no_network)
    item = set_items(db, run, RunItemStatus.AWAITING_APPROVAL)[0]
    db.commit()
    user = reviewer(db)
    decide(db, item.id, user, "approved", None, NOW)

    with pytest.raises(NotAwaitingDecision, match="resuming"):
        decide(db, item.id, user, "rejected", "changed my mind", NOW)

    assert len(list(db.scalars(select(Approval)))) == 1


def test_the_unique_approval_is_the_second_guard(db: Session, campaign, settings, no_network):
    run = collected_run(db, campaign, settings, no_network)
    item = set_items(db, run, RunItemStatus.AWAITING_APPROVAL)[0]
    user = reviewer(db)
    # As if another decision had slipped past the status check.
    db.add(Approval(run_item_id=item.id, reviewer_id=user.id, decision="approved"))
    db.commit()

    with pytest.raises(NotAwaitingDecision, match="already has a decision"):
        decide(db, item.id, user, "approved", None, NOW)


def test_decide_checks_the_input(db: Session, campaign, settings, no_network) -> None:
    run = collected_run(db, campaign, settings, no_network)
    item = set_items(db, run, RunItemStatus.AWAITING_APPROVAL)[0]
    db.commit()
    user = reviewer(db)

    with pytest.raises(ValueError, match="needs a reason"):
        decide(db, item.id, user, "rejected", " ", NOW)
    with pytest.raises(LookupError):
        decide(db, 9999, user, "approved", None, NOW)
    pending = set_items(db, run, RunItemStatus.AWAITING_APPROVAL, RunItemStatus.PENDING)[1]
    db.commit()
    with pytest.raises(NotAwaitingDecision, match="pending"):
        decide(db, pending.id, user, "approved", None, NOW)
