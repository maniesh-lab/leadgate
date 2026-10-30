"""Tests for the fake run history (`app.db.fake_history`) and `fake-data --history`."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.cli import main
from app.config import Settings
from app.db.fake_data import insert_fake_leads
from app.db.fake_history import REVIEWERS, insert_fake_history
from app.db.models import (
    Approval,
    AuditLog,
    Job,
    JobStatus,
    Lead,
    LeadStatus,
    Route,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
    StepStatus,
    User,
)

NOW = datetime.now(UTC)


@pytest.fixture
def history(db: Session) -> Session:
    insert_fake_leads(db, 400, seed=3)
    insert_fake_history(db, now=NOW, seed=3)
    return db


def count(db: Session, model: type, *where: object) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def test_every_fake_lead_gets_one_item(history: Session) -> None:
    lead_ids = history.scalars(select(RunItem.lead_id)).all()
    assert len(lead_ids) == len(set(lead_ids)) == 400


def test_a_second_call_adds_nothing(history: Session) -> None:
    counts = insert_fake_history(history, now=NOW)
    assert counts.runs == 0
    assert count(history, RunItem) == 400


def test_lead_status_follows_its_item(history: Session) -> None:
    expected = {
        Route.NOTIFY: LeadStatus.APPROVED,
        Route.ESCALATE: LeadStatus.ESCALATED,
        Route.SKIP: LeadStatus.REJECTED,
    }
    for item, lead in history.execute(select(RunItem, Lead).join(Lead)):
        if item.status == RunItemStatus.FAILED:
            assert lead.status == LeadStatus.NEW
        elif item.status == RunItemStatus.AWAITING_APPROVAL:
            assert lead.status == LeadStatus.IN_REVIEW
        else:
            assert lead.status == expected[item.route]
            assert lead.score == item.score


def test_decided_items_have_an_approval_and_a_closed_gate(history: Session) -> None:
    done = count(history, RunItem, RunItem.status == RunItemStatus.DONE)
    assert count(history, Approval) == done
    assert count(history, Approval, Approval.created_at > NOW) == 0
    assert (
        count(
            history,
            RunStep,
            RunStep.node == "approval_gate",
            RunStep.status == StepStatus.COMPLETED,
        )
        == done
    )
    reviewers = history.scalars(select(User.email).where(User.email.in_(REVIEWERS))).all()
    assert sorted(reviewers) == sorted(REVIEWERS)


def test_rejections_have_a_reason(history: Session) -> None:
    rejected = history.scalars(select(Approval).where(Approval.decision == "rejected")).all()
    assert rejected
    assert all(approval.reason for approval in rejected)


def test_runs_match_their_items(history: Session) -> None:
    for run in history.scalars(select(Run)):
        statuses = {
            item.status for item in history.scalars(select(RunItem).where(RunItem.run_id == run.id))
        }
        waiting = RunItemStatus.AWAITING_APPROVAL in statuses
        assert run.status == (RunStatus.WAITING_APPROVAL if waiting else RunStatus.COMPLETED)
        assert (run.finished_at is None) == waiting


def test_audit_rows_tell_each_items_story(history: Session) -> None:
    notified = history.scalars(select(RunItem).where(RunItem.route == Route.NOTIFY)).first()
    actions = history.scalars(
        select(AuditLog.action)
        .where(AuditLog.run_item_id == notified.id)
        .order_by(AuditLog.occurred_at, AuditLog.id)
    ).all()
    assert actions == [
        "lead.collected",
        "lead.enriched",
        "lead.extracted",
        "lead.checked",
        "lead.scored",
        "approval.requested",
        "approval.approved",
        "lead.decided",
        "lead.routed",
        "lead.acted",
        "crm.written",
    ]
    # Every audit row and step happens before "now".
    assert count(history, AuditLog, AuditLog.occurred_at > NOW + timedelta(minutes=5)) == 0


def test_jobs_are_finished(history: Session) -> None:
    assert count(history, Job, Job.status == JobStatus.QUEUED) == 0
    assert count(history, Job, Job.type == "process_item") == 400


def test_cli_fake_data_with_history(
    engine: Engine, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["fake-data", "--leads", "30", "--seed", "4", "--history"], settings) == 0
    out = capsys.readouterr().out
    assert "inserted 30 fake leads" in out
    assert "history: " in out
    assert "30 run_items" in out
