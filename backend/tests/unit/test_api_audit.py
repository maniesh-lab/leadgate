"""Tests for `GET /api/audit`: filters, time range, order and pages."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditLog


def add_row(db: Session, when: datetime, action: str, **fields) -> AuditLog:
    values = {
        "actor_type": ActorType.SYSTEM,
        "actor": "worker",
        "entity_type": "run",
        "entity_id": 1,
        "run_id": 1,
    }
    row = AuditLog(occurred_at=when, action=action, **(values | fields))
    db.add(row)
    db.commit()
    return row


def at(hour: int) -> datetime:
    return datetime(2026, 10, 30, hour, 0, tzinfo=UTC)


def test_filters_and_order(client: TestClient, db: Session, as_reviewer) -> None:
    add_row(db, at(9), "run.created", actor_type=ActorType.USER, actor="admin@example.com")
    add_row(
        db,
        at(10),
        "lead.scored",
        actor_type=ActorType.AGENT,
        actor="score",
        entity_type="lead",
        entity_id=7,
    )
    add_row(
        db,
        at(11),
        "approval.approved",
        actor="rev@example.com",
        entity_type="run_item",
        entity_id=3,
    )
    add_row(db, at(12), "run.created", run_id=2, entity_id=2)

    def actions(query: str = "") -> list[str]:
        response = client.get(f"/api/audit?{query}", headers=as_reviewer)
        assert response.status_code == 200, response.text
        return [row["action"] for row in response.json()["items"]]

    assert actions() == ["run.created", "approval.approved", "lead.scored", "run.created"]
    assert actions("run_id=1") == ["approval.approved", "lead.scored", "run.created"]
    assert actions("entity_type=lead&entity_id=7") == ["lead.scored"]
    assert actions("actor=score") == ["lead.scored"]
    assert actions("action=run.created") == ["run.created", "run.created"]
    assert actions("from=2026-10-30T10:00:00Z&to=2026-10-30T12:00:00Z") == [
        "approval.approved",
        "lead.scored",
    ]
    # A time without a timezone is UTC.
    assert actions("from=2026-10-30T11:00:00") == ["run.created", "approval.approved"]

    page = client.get("/api/audit?page_size=1&page=4", headers=as_reviewer).json()
    assert page["total"] == 4
    assert page["items"][0]["actor"] == "admin@example.com"
    assert page["items"][0]["details"] == {}


def test_audit_needs_a_login_and_real_times(client: TestClient, db: Session, as_reviewer) -> None:
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit?from=yesterday", headers=as_reviewer).status_code == 422
