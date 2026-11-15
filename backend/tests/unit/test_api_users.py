"""Tests for turning accounts off and on (`PATCH /api/users/{id}`).

Listing and creating users are tested in `test_api_auth.py`.
"""

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, User, UserRole
from tests.conftest import PASSWORD, add_user, auth_headers
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
    # Turning an admin back on needs your password again: a former admin's still works.
    assert set_active(client, as_admin, other, True).status_code == 400
    on = client.patch(
        f"/api/users/{other.id}",
        json={"is_active": True, "confirm": {"password": PASSWORD}},
        headers=as_admin,
    )
    assert on.status_code == 200 and on.json()["is_active"] is True


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


# --- Changes a stolen login mustn't make: they need the admin's own password again ---


def failed_logins(db: Session) -> int:
    db.expire_all()
    return len(db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).all())


def test_a_new_admin_needs_your_password_again(client: TestClient, db: Session, as_admin) -> None:
    body = {"email": "second@example.com", "password": "long-enough", "role": "admin"}

    missing = client.post("/api/users", json=body, headers=as_admin)
    assert missing.status_code == 400
    assert "re-enter your password" in missing.json()["error"]["message"]
    wrong = client.post(
        "/api/users", json={**body, "confirm": {"password": "not-it"}}, headers=as_admin
    )
    assert wrong.status_code == 400
    assert failed_logins(db) == 1  # counts like a wrong login, so it can't be guessed
    assert db.scalar(select(User).where(User.email == "second@example.com")) is None

    right = client.post(
        "/api/users", json={**body, "confirm": {"password": PASSWORD}}, headers=as_admin
    )
    assert right.status_code == 201 and right.json()["role"] == "admin"
    # A reviewer still needs nothing more.
    reviewer = {"email": "third@example.com", "password": "long-enough"}
    assert client.post("/api/users", json=reviewer, headers=as_admin).status_code == 201


def test_another_admins_reset_link_needs_your_password_again(
    client: TestClient, db: Session, as_admin
) -> None:
    other = add_user(db, "other-admin@example.com", UserRole.ADMIN)
    path = f"/api/users/{other.id}/reset-link"

    assert client.post(path, headers=as_admin).status_code == 400
    assert client.post(path, json={"password": "not-it"}, headers=as_admin).status_code == 400
    response = client.post(path, json={"password": PASSWORD}, headers=as_admin)
    assert response.status_code == 200 and response.json()["token"]


def test_another_admins_two_factor_off_needs_your_password_and_code(
    client: TestClient, db: Session, admin: User, as_admin
) -> None:
    other = add_user(db, "other-admin@example.com", UserRole.ADMIN)
    other.totp_enabled, other.totp_secret = True, pyotp.random_base32()
    admin.totp_enabled, admin.totp_secret = True, pyotp.random_base32()
    db.commit()
    path = f"/api/users/{other.id}/two-factor/off"

    assert client.post(path, headers=as_admin).status_code == 400
    no_code = client.post(path, json={"password": PASSWORD}, headers=as_admin)
    assert (no_code.status_code, no_code.json()["error"]["message"]) == (
        400,
        "enter a code from your app too",
    )
    wrong_code = client.post(path, json={"password": PASSWORD, "code": "000000"}, headers=as_admin)
    assert wrong_code.status_code == 400
    code = pyotp.TOTP(admin.totp_secret).now()
    off = client.post(path, json={"password": PASSWORD, "code": code}, headers=as_admin)
    assert off.status_code == 200 and off.json()["two_factor_enabled"] is False


def test_a_code_spent_on_a_refused_request_cant_be_used_again(
    client: TestClient, db: Session, admin: User, as_admin
) -> None:
    admin.totp_enabled, admin.totp_secret = True, pyotp.random_base32()
    db.commit()
    code = pyotp.TOTP(admin.totp_secret).now()
    body = {"email": "admin@example.com", "password": "long-enough", "role": "admin"}
    confirm = {"password": PASSWORD, "code": code}

    taken = client.post("/api/users", json={**body, "confirm": confirm}, headers=as_admin)
    assert taken.status_code == 409  # the email exists, after the code was checked

    again = {**body, "email": "new-admin@example.com", "confirm": confirm}
    assert client.post("/api/users", json=again, headers=as_admin).status_code == 400
