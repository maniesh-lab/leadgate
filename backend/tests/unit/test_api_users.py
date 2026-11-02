"""Tests for turning accounts off and on (`PATCH /api/users/{id}`).

Listing and creating users are tested in `test_api_auth.py`.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, User, UserRole
from tests.conftest import add_user, auth_headers
from tests.unit.conftest import login


def set_active(client: TestClient, headers: dict, user: User, active: bool):
    return client.patch(f"/api/users/{user.id}", json={"is_active": active}, headers=headers)


def test_turning_a_reviewer_off_ends_its_logins_for_good(
    client: TestClient, db: Session, as_admin, reviewer: User
) -> None:
    old_token = auth_headers(reviewer)
    assert client.get("/api/auth/me", headers=old_token).status_code == 200

    off = set_active(client, as_admin, reviewer, False)
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert client.get("/api/auth/me", headers=old_token).status_code == 401

    on = set_active(client, as_admin, reviewer, True)
    assert on.status_code == 200 and on.json()["is_active"] is True
    # Back on, but the token from before stays ended: the reviewer logs in again.
    assert client.get("/api/auth/me", headers=old_token).status_code == 401
    assert client.get("/api/auth/me", headers=login(client, reviewer.email)).status_code == 200

    rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [(row.action, row.entity_id) for row in rows] == [
        ("user.deactivated", reviewer.id),
        ("user.activated", reviewer.id),
    ]
    assert rows[0].details == {"email": reviewer.email, "role": "reviewer"}


def test_no_change_writes_no_audit_row(
    client: TestClient, db: Session, as_admin, reviewer: User
) -> None:
    assert set_active(client, as_admin, reviewer, True).status_code == 200
    assert db.scalars(select(AuditLog)).all() == []


def test_you_cant_turn_yourself_off(client: TestClient, db: Session, as_admin, admin: User) -> None:
    response = set_active(client, as_admin, admin, False)
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "you can't turn off your own account"
    db.refresh(admin)
    assert admin.is_active


def test_an_admin_can_turn_another_admin_off(client: TestClient, db: Session, as_admin) -> None:
    other = add_user(db, "other-admin@example.com", UserRole.ADMIN)
    assert set_active(client, as_admin, other, False).status_code == 200
    # Now `admin` is the last active admin; the API still lets it turn the other back on.
    assert set_active(client, as_admin, other, True).status_code == 200


def test_only_admins_change_accounts(
    client: TestClient, as_reviewer, admin: User, reviewer: User
) -> None:
    assert set_active(client, as_reviewer, admin, False).status_code == 403
    assert set_active(client, as_reviewer, reviewer, False).status_code == 403
    assert set_active(client, {}, reviewer, False).status_code == 401


def test_unknown_user_and_bad_body(client: TestClient, as_admin, reviewer: User) -> None:
    assert (
        client.patch("/api/users/999", json={"is_active": False}, headers=as_admin).status_code
        == 404
    )
    bad = client.patch(f"/api/users/{reviewer.id}", json={"role": "admin"}, headers=as_admin)
    assert bad.status_code == 422
