"""Model tests on SQLite: defaults, JSON round trips, and the constraints that guard the data."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db.models import (
    ActorType,
    Approval,
    AuditLog,
    Campaign,
    Decision,
    Job,
    JobStatus,
    JobType,
    Lead,
    LeadStatus,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
    RunTrigger,
    ScoringRubric,
    StepStatus,
    User,
    UserRole,
)


def make_user(db: Session, email: str = "admin@example.com") -> User:
    user = User(email=email, password_hash="x", role=UserRole.ADMIN)
    db.add(user)
    db.flush()
    return user


def make_lead(db: Session, source_key: str = "osm:node/1") -> Lead:
    lead = Lead(
        source="osm",
        source_ref=source_key.split(":", 1)[1],
        source_key=source_key,
        raw={"tags": {"amenity": "dentist"}},
        name="Smile Dental",
    )
    db.add(lead)
    db.flush()
    return lead


def make_run_item(db: Session) -> RunItem:
    """Builds the whole chain a run item needs: user → rubric → campaign → run → lead."""
    user = make_user(db)
    rubric = ScoringRubric(name="general-b2b", version=1, rules=[], created_by=user.id)
    db.add(rubric)
    db.flush()
    campaign = Campaign(
        name="Austin dentists",
        niche="dentist",
        location="Austin, TX",
        sources=["osm"],
        rubric_id=rubric.id,
        created_by=user.id,
    )
    db.add(campaign)
    db.flush()
    run = Run(campaign_id=campaign.id, trigger=RunTrigger.MANUAL, rubric_id=rubric.id)
    lead = make_lead(db)
    db.add(run)
    db.flush()
    item = RunItem(run_id=run.id, lead_id=lead.id, thread_id=f"run-{run.id}-lead-{lead.id}")
    db.add(item)
    db.flush()
    return item


def test_full_chain_saves_with_defaults(db: Session) -> None:
    item = make_run_item(db)
    db.add(RunStep(run_item_id=item.id, node="enrich", status=StepStatus.STARTED))
    db.add(Approval(run_item_id=item.id, reviewer_id=1, decision=Decision.APPROVED))
    db.commit()

    run = db.get(Run, item.run_id)
    assert run is not None
    assert run.status == RunStatus.QUEUED
    assert run.stats == {}
    assert item.status == RunItemStatus.PENDING
    assert item.flags == []
    lead = db.get(Lead, item.lead_id)
    assert lead is not None
    assert lead.status == LeadStatus.NEW
    assert lead.opt_out is False
    assert lead.raw == {"tags": {"amenity": "dentist"}}


def test_timestamps_come_back_utc_aware(db: Session) -> None:
    user = make_user(db)
    db.commit()
    db.refresh(user)
    assert user.created_at.tzinfo is UTC
    assert user.is_active is True


def test_naive_datetime_is_rejected(db: Session) -> None:
    lead = make_lead(db)
    lead.enriched_at = datetime(2026, 1, 1)  # no timezone
    with pytest.raises(StatementError, match="naive datetime"):
        db.flush()


def test_enum_values_are_stored_as_lowercase_strings(db: Session) -> None:
    make_user(db)
    db.commit()
    assert db.scalar(text("SELECT role FROM users")) == "admin"


def test_check_constraint_blocks_unknown_enum_value(db: Session) -> None:
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO users (email, password_hash, role) VALUES ('a', 'x', 'boss')"))


def test_source_key_is_unique(db: Session) -> None:
    make_lead(db, "osm:node/1")
    with pytest.raises(IntegrityError):
        make_lead(db, "osm:node/1")


def test_score_must_be_0_to_100(db: Session) -> None:
    lead = make_lead(db)
    lead.score = 101
    with pytest.raises(IntegrityError):
        db.flush()


def test_foreign_keys_are_enforced_on_sqlite(db: Session) -> None:
    db.add(Run(campaign_id=999, trigger=RunTrigger.MANUAL, rubric_id=999))
    with pytest.raises(IntegrityError):
        db.flush()


def test_lead_appears_once_per_run(db: Session) -> None:
    item = make_run_item(db)
    db.add(RunItem(run_id=item.run_id, lead_id=item.lead_id, thread_id="another-thread"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_reject_needs_a_reason(db: Session) -> None:
    item = make_run_item(db)
    db.add(Approval(run_item_id=item.id, reviewer_id=1, decision=Decision.REJECTED, reason="  "))
    with pytest.raises(IntegrityError):
        db.flush()


def test_reject_with_reason_is_fine(db: Session) -> None:
    item = make_run_item(db)
    db.add(
        Approval(
            run_item_id=item.id, reviewer_id=1, decision=Decision.REJECTED, reason="Closed down"
        )
    )
    db.flush()


def test_second_decision_on_same_item_fails(db: Session) -> None:
    item = make_run_item(db)
    db.add(Approval(run_item_id=item.id, reviewer_id=1, decision=Decision.APPROVED))
    db.flush()
    db.add(Approval(run_item_id=item.id, reviewer_id=1, decision=Decision.APPROVED))
    with pytest.raises(IntegrityError):
        db.flush()


def test_audit_row_and_job_defaults(db: Session) -> None:
    db.add(
        AuditLog(
            actor_type=ActorType.SYSTEM,
            actor="cli",
            action="run.started",
            entity_type="run",
            entity_id=1,
        )
    )
    db.add(Job(type=JobType.START_RUN, payload={"run_id": 1}))
    db.commit()

    audit = db.scalars(select(AuditLog)).one()
    assert audit.id == 1
    assert audit.details == {}
    job = db.scalars(select(Job)).one()
    assert (job.status, job.attempts, job.max_attempts) == (JobStatus.QUEUED, 0, 3)
    assert job.run_after.tzinfo is UTC
