"""The reports (`app.reporting.queries`) on real Postgres, with exact numbers.

A small world is built by hand: one campaign with five leads in one run (four decided,
one waiting), a second campaign with no runs, two reviewers. Each report is checked
against numbers worked out from that world by hand. Then the API answers through
`GET /api/reports/...`, and every report runs on a fake history (`fake-data --history`).

Skipped unless `TEST_POSTGRES_URL` is set (see `conftest.py` in this folder).
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.fake_data import insert_fake_leads
from app.db.fake_history import insert_fake_history
from app.db.models import (
    ActorType,
    Approval,
    AuditLog,
    Campaign,
    Decision,
    Lead,
    LeadStatus,
    Route,
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
from app.db.seed import GENERAL_B2B_RULES
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from app.reporting.queries import REPORTS, ReportName, run_report
from tests.conftest import TEST_JWT_SECRET, add_user, auth_headers
from tests.migrations import alembic_config

TODAY = date(2026, 10, 30)


def at(days_ago: int, hour: int, minute: int = 0) -> datetime:
    day = TODAY - timedelta(days=days_ago)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.fixture
def db(postgres_url: str) -> Iterator[Session]:
    command.upgrade(alembic_config(postgres_url), "head")
    engine = make_engine(postgres_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def world(db: Session) -> dict[str, Any]:
    """The hand-built world (see the module docstring); returns the ids tests need."""
    ana = add_user(db, "ana@example.com", UserRole.REVIEWER)
    bo = add_user(db, "bo@example.com", UserRole.REVIEWER)
    rubric = ScoringRubric(name="general-b2b", version=1, rules=GENERAL_B2B_RULES)
    db.add(rubric)
    db.flush()
    first = Campaign(name="A", niche="dentist", location="Austin, TX", rubric_id=rubric.id)
    second = Campaign(name="B", niche="gym", location="Austin, TX", rubric_id=rubric.id)
    db.add_all([first, second])
    db.flush()
    run = Run(
        campaign_id=first.id,
        trigger=RunTrigger.MANUAL,
        rubric_id=rubric.id,
        status=RunStatus.WAITING_APPROVAL,
    )
    db.add(run)
    db.flush()

    # name, area, score, lead status, email, item status, route (L2 and L4 get a flag below)
    rows = [
        ("L1", "Downtown", 90, LeadStatus.APPROVED, "a@l1.com", RunItemStatus.DONE, Route.NOTIFY),
        ("L2", "Downtown", 70, LeadStatus.REJECTED, "a@l2.com", RunItemStatus.DONE, Route.SKIP),
        ("L3", "Downtown", 50, LeadStatus.IN_REVIEW, None, RunItemStatus.AWAITING_APPROVAL, None),
        ("L4", "North", 10, LeadStatus.ESCALATED, None, RunItemStatus.DONE, Route.ESCALATE),
        ("L5", "North", 30, LeadStatus.APPROVED, None, RunItemStatus.DONE, Route.NOTIFY),
    ]
    items: dict[str, RunItem] = {}
    for name, area, score, status, email, item_status, route in rows:
        lead = Lead(
            source="fake",
            source_ref=name,
            source_key=f"fake:{name}",
            raw={},
            name=name,
            email=email,
            area=area,
            city="Austin",
            score=score,
            status=status,
        )
        db.add(lead)
        db.flush()
        flags = ["possible_duplicate:1"] if name in ("L2", "L4") else []
        item = RunItem(
            run_id=run.id,
            lead_id=lead.id,
            thread_id=f"t-{name}",
            status=item_status,
            route=route,
            flags=flags,
            score=score,
        )
        db.add(item)
        db.flush()
        items[name] = item

    # (lead, reviewer, decision, gate reached, decided): waits ana 60 + 30, bo 60 + 120
    decisions = [
        ("L1", ana, Decision.APPROVED, at(0, 9), at(0, 10)),
        ("L2", ana, Decision.REJECTED, at(1, 9, 30), at(1, 10)),
        ("L4", bo, Decision.APPROVED, at(0, 10), at(0, 11)),
        ("L5", bo, Decision.APPROVED, at(40, 8), at(40, 10)),
    ]
    for name, reviewer, decision, reached, decided in decisions:
        reason = "not a fit" if decision == Decision.REJECTED else None
        db.add(
            Approval(
                run_item_id=items[name].id,
                reviewer_id=reviewer.id,
                decision=decision,
                reason=reason,
                created_at=decided,
            )
        )
        db.add(gate_step(items[name], StepStatus.COMPLETED, reached, 5))
    db.add(gate_step(items["L3"], StepStatus.STARTED, at(0, 12), None))
    for name, status, duration in [
        ("L1", StepStatus.COMPLETED, 100),
        ("L2", StepStatus.COMPLETED, 300),
        ("L4", StepStatus.FAILED, 50),
    ]:
        db.add(
            RunStep(
                run_item_id=items[name].id,
                node="enrich",
                status=status,
                started_at=at(1, 8),
                duration_ms=duration,
            )
        )

    audit = [("lead.collected", at(1, 8), {})] * 4
    audit += [
        ("approval.rejected", at(1, 10), {}),
        ("approval.approved", at(0, 10), {}),
        ("approval.approved", at(0, 11), {}),
        ("lead.routed", at(0, 11, 1), {"route": "escalate"}),
        ("lead.routed", at(0, 10, 1), {"route": "notify"}),
        ("approval.approved", at(40, 10), {}),  # outside a 2-day window
    ]
    for action, moment, details in audit:
        db.add(
            AuditLog(
                occurred_at=moment,
                actor_type=ActorType.AGENT,
                actor="test",
                action=action,
                entity_type="lead",
                run_id=run.id,
                details=details,
            )
        )
    db.commit()
    lead_ids = {name: item.lead_id for name, item in items.items()}
    return {"first": first.id, "second": second.id, "leads": lead_ids}


def gate_step(
    item: RunItem, status: StepStatus, reached: datetime, duration: int | None
) -> RunStep:
    return RunStep(
        run_item_id=item.id,
        node="approval_gate",
        status=status,
        started_at=reached,
        duration_ms=duration,
    )


def report(db: Session, name: ReportName, **params: int) -> list[dict[str, Any]]:
    return run_report(db, name, TODAY, **params)


def test_daily_funnel(db: Session, world: dict) -> None:
    assert report(db, ReportName.DAILY_FUNNEL, days=2) == [
        {"day": date(2026, 10, 29), "collected": 4, "approved": 0, "rejected": 1, "escalated": 0},
        {"day": date(2026, 10, 30), "collected": 0, "approved": 2, "rejected": 0, "escalated": 1},
    ]
    # A quiet day still gets a row (the days come from generate_series).
    assert len(report(db, ReportName.DAILY_FUNNEL, days=7)) == 7


def test_approved_running_total_starts_from_earlier_approvals(db: Session, world: dict) -> None:
    assert report(db, ReportName.APPROVED_RUNNING_TOTAL, days=2) == [
        {"day": date(2026, 10, 29), "approved": 0, "running_total": 1},
        {"day": date(2026, 10, 30), "approved": 2, "running_total": 3},
    ]


def test_top_leads_per_area_leaves_out_rejected_leads(db: Session, world: dict) -> None:
    rows = report(db, ReportName.TOP_LEADS_PER_AREA, top=1)
    assert [(row["area"], row["rank"], row["name"]) for row in rows] == [
        ("Downtown", 1, "L1"),
        ("North", 1, "L5"),
    ]
    rows = report(db, ReportName.TOP_LEADS_PER_AREA, top=2)
    # L2 (70, rejected) is left out, so Downtown's second is L3.
    assert [row["name"] for row in rows] == ["L1", "L3", "L5", "L4"]


def test_reviewer_stats(db: Session, world: dict) -> None:
    assert report(db, ReportName.REVIEWER_STATS) == [
        {
            "reviewer": "ana@example.com",
            "decisions": 2,
            "approved": 1,
            "rejected": 1,
            "approved_pct": 50.0,
            "median_wait_minutes": 45.0,
        },
        {
            "reviewer": "bo@example.com",
            "decisions": 2,
            "approved": 2,
            "rejected": 0,
            "approved_pct": 100.0,
            "median_wait_minutes": 90.0,
        },
    ]


def test_node_durations(db: Session, world: dict) -> None:
    assert report(db, ReportName.NODE_DURATIONS) == [
        # avg of 100 and 300; p95 interpolated: 100 + 0.95 * 200; the failed step isn't timed
        {"node": "enrich", "completed": 2, "failed": 1, "avg_ms": 200, "p95_ms": 290},
        # the open (waiting) gate step has no duration yet
        {"node": "approval_gate", "completed": 4, "failed": 0, "avg_ms": 5, "p95_ms": 5},
    ]


def test_score_distribution_keeps_every_bucket(db: Session, world: dict) -> None:
    assert report(db, ReportName.SCORE_DISTRIBUTION) == [
        {"bucket": "0-19", "leads": 1, "approved": 0, "rejected": 0},
        {"bucket": "20-39", "leads": 1, "approved": 1, "rejected": 0},
        {"bucket": "40-59", "leads": 1, "approved": 0, "rejected": 0},
        {"bucket": "60-79", "leads": 1, "approved": 0, "rejected": 1},
        {"bucket": "80-100", "leads": 1, "approved": 1, "rejected": 0},
    ]


def test_campaign_conversion(db: Session, world: dict) -> None:
    assert report(db, ReportName.CAMPAIGN_CONVERSION) == [
        {
            "campaign_id": world["first"],
            "name": "A",
            "collected": 5,
            "approved": 3,
            "approved_pct": 60.0,
            "emails_found_pct": 40.0,
            "duplicates_pct": 40.0,
        },
        # No runs yet: zeros, and no percentages (nothing to divide by).
        {
            "campaign_id": world["second"],
            "name": "B",
            "collected": 0,
            "approved": 0,
            "approved_pct": None,
            "emails_found_pct": None,
            "duplicates_pct": None,
        },
    ]


def test_every_report_runs_on_a_fake_history(db: Session) -> None:
    insert_fake_leads(db, 300, seed=5)
    counts = insert_fake_history(db, seed=5)
    # The fake history is dated up to the real clock, so the reports must end on the real
    # today, not the fixed TODAY of the hand-built world (a lead made "today" fell out).
    today = datetime.now(UTC).date()
    for name in REPORTS:
        assert run_report(db, name, today), name
    funnel = run_report(db, ReportName.DAILY_FUNNEL, today, days=365)
    assert sum(row["collected"] for row in funnel) == counts.run_items
    conversion = run_report(db, ReportName.CAMPAIGN_CONVERSION, today)
    assert sum(row["collected"] for row in conversion) == counts.run_items


@pytest.fixture
def client(postgres_url: str, db: Session) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, database_url=postgres_url, jwt_secret=TEST_JWT_SECRET)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_report_endpoint_on_postgres(client: TestClient, db: Session, world: dict) -> None:
    reviewer = db.query(User).filter_by(email="ana@example.com").one()
    response = client.get(
        "/api/reports/top_leads_per_area?top=1&days=9", headers=auth_headers(reviewer)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "top_leads_per_area"
    assert body["params"] == {"top": 1}  # days isn't one of its parameters
    assert [row["name"] for row in body["rows"]] == ["L1", "L5"]

    response = client.get("/api/reports/daily_funnel?days=3", headers=auth_headers(reviewer))
    assert response.status_code == 200
    days = [row["day"] for row in response.json()["rows"]]
    assert len(days) == 3
    assert days == sorted(days)  # ISO dates, oldest first
