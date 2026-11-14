"""Tests for `GET /api/audit`: filters, time range, order and pages."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditLog, User


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
    # Not a user in this database, and only admins see other users' emails.
    assert page["items"][0]["actor"] == "a user"
    assert page["items"][0]["details"] == {}


def test_login_rows_are_for_admins_only(
    client: TestClient, db: Session, as_reviewer, as_admin
) -> None:
    # A failed login keeps the typed email + the caller's IP; the public demo's reviewer
    # login must not be able to read them.
    add_row(
        db,
        at(9),
        "auth.login_failed",
        actor_type=ActorType.USER,
        actor="someone@example.com",
        entity_type="user",
        entity_id=None,
        run_id=None,
        details={"ip": "203.0.113.7"},
    )
    add_row(db, at(10), "run.created")

    def actions(headers: dict[str, str], query: str = "") -> list[str]:
        response = client.get(f"/api/audit?{query}", headers=headers)
        assert response.status_code == 200, response.text
        return [row["action"] for row in response.json()["items"]]

    assert actions(as_reviewer) == ["run.created"]
    assert actions(as_reviewer, "action=auth.login_failed") == []
    assert actions(as_admin) == ["run.created", "auth.login_failed"]


def test_audit_needs_a_login_and_real_times(client: TestClient, db: Session, as_reviewer) -> None:
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit?from=yesterday", headers=as_reviewer).status_code == 422


def test_account_and_admin_tool_rows_are_for_admins_only(
    client: TestClient, db: Session, as_reviewer, as_admin
) -> None:
    # They name admins, users and uploaded files; the demo's public reviewer must not see them.
    hidden = [
        "user.deactivated",
        "user.created",
        "campaign.created",
        "csv.uploaded",
        "leads.exported",
    ]
    for minute, action in enumerate(hidden):
        add_row(db, at(minute), action, actor_type=ActorType.USER, actor="admin@example.com")
    add_row(db, at(9), "run_item.retried", actor_type=ActorType.USER, actor="admin@example.com")

    def actions(headers: dict[str, str]) -> list[str]:
        return [row["action"] for row in client.get("/api/audit", headers=headers).json()["items"]]

    assert actions(as_reviewer) == ["run_item.retried"]
    assert sorted(actions(as_admin)) == sorted([*hidden, "run_item.retried"])


def test_only_admins_see_other_users_emails(
    client: TestClient, db: Session, admin: User, reviewer: User, as_admin, as_reviewer
) -> None:
    add_row(db, at(9), "run.created", actor_type=ActorType.USER, actor=admin.email)
    add_row(db, at(10), "approval.approved", actor_type=ActorType.USER, actor=reviewer.email)

    def actors(headers: dict[str, str]) -> list[str]:
        return [row["actor"] for row in client.get("/api/audit", headers=headers).json()["items"]]

    assert actors(as_admin) == [reviewer.email, admin.email]
    assert actors(as_reviewer) == [reviewer.email, f"user {admin.id}"]  # their own only


def test_a_reviewer_cant_find_out_whose_email_is_whose(
    client: TestClient, db: Session, admin: User, reviewer: User, as_admin, as_reviewer
) -> None:
    add_row(db, at(9), "run.created", actor_type=ActorType.USER, actor=admin.email)

    def total(query: str, headers: dict[str, str]) -> int:
        return client.get(f"/api/audit?{query}", headers=headers).json()["total"]

    # A guessed email finds nothing for a reviewer (it would confirm the email is a user's);
    # the label they see does, and an admin can search by email.
    assert total(f"actor={admin.email}", as_reviewer) == 0
    assert total(f"actor=user {admin.id}", as_reviewer) == 1
    assert total(f"actor={admin.email}", as_admin) == 1
    assert total(f"actor=user {admin.id}", as_admin) == 1
    assert total("actor=user 999", as_reviewer) == 0
