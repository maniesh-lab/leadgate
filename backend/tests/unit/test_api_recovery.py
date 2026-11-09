"""Account recovery: one-time reset links, unlocking a locked login (`app.services.accounts`)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, PasswordResetLink, User
from app.services.accounts import ResetLinkRefused, create_reset_link
from tests.conftest import PASSWORD, auth_headers


def login(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def make_link(client: TestClient, user: User, as_admin: dict[str, str]) -> str:
    response = client.post(f"/api/users/{user.id}/reset-link", headers=as_admin)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()["token"]


def reset(client: TestClient, token: str, password: str = "brand-new-password"):
    return client.post("/api/auth/reset-password", json={"token": token, "new_password": password})


def test_a_reset_link_sets_a_new_password_once(
    client: TestClient, db: Session, reviewer: User, as_admin: dict[str, str]
) -> None:
    old_token = auth_headers(reviewer)
    token = make_link(client, reviewer, as_admin)

    assert reset(client, token).status_code == 204

    assert login(client, "reviewer@example.com", "brand-new-password").status_code == 200
    assert login(client, "reviewer@example.com", PASSWORD).status_code == 401
    # Older logins end, and the link works only once.
    assert client.get("/api/auth/me", headers=old_token).status_code == 401
    again = reset(client, token, "another-password")
    assert again.status_code == 400
    assert "ask an admin for a new one" in again.json()["error"]["message"]


def test_only_the_tokens_hash_is_stored_and_the_audit_log_never_has_it(
    client: TestClient, db: Session, reviewer: User, as_admin: dict[str, str]
) -> None:
    token = make_link(client, reviewer, as_admin)
    reset(client, token)

    db.expire_all()
    link = db.scalars(select(PasswordResetLink)).one()
    assert link.token_hash != token and len(link.token_hash) == 64
    rows = db.scalars(select(AuditLog).where(AuditLog.entity_id == reviewer.id)).all()
    assert token not in json.dumps([row.details for row in rows])
    actions = [row.action for row in rows]
    assert {"user.reset_link_created", "user.password_reset"} <= set(actions)


def test_an_expired_or_replaced_link_doesnt_work(
    client: TestClient, db: Session, reviewer: User, as_admin: dict[str, str]
) -> None:
    first = make_link(client, reviewer, as_admin)
    second = make_link(client, reviewer, as_admin)
    assert reset(client, first).status_code == 400  # a new link ends the old one

    link = db.scalars(select(PasswordResetLink).where(PasswordResetLink.used_at.is_(None))).one()
    link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert reset(client, second).status_code == 400
    assert reset(client, "made-up-token").status_code == 400


def test_reset_links_are_refused_for_an_account_thats_off(
    client: TestClient, db: Session, reviewer: User, as_admin: dict[str, str], settings
) -> None:
    reviewer.is_active = False
    db.commit()
    off = client.post(f"/api/users/{reviewer.id}/reset-link", headers=as_admin)
    assert off.status_code == 409
    assert "turn it on first" in off.json()["error"]["message"]

    # A link made while it was on stops working once it's off.
    reviewer.is_active = True
    db.commit()
    token = make_link(client, reviewer, as_admin)
    reviewer.is_active = False
    db.commit()
    assert reset(client, token).status_code == 400


def test_only_admins_make_links_unlock_or_turn_off_two_factor(
    client: TestClient, admin: User, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    for path in ("reset-link", "unlock", "two-factor/off"):
        response = client.post(f"/api/users/{admin.id}/{path}", headers=as_reviewer)
        assert response.status_code == 403, path
        assert client.post(f"/api/users/{admin.id}/{path}").status_code == 401, path


def test_unlocking_lifts_the_email_lock_but_counts_later_failures(
    client: TestClient, reviewer: User, as_admin: dict[str, str]
) -> None:
    for _ in range(5):
        login(client, "reviewer@example.com", "wrong-password")
    assert login(client, "reviewer@example.com", PASSWORD).status_code == 429
    users = client.get("/api/users", headers=as_admin).json()["items"]
    locked = next(user for user in users if user["id"] == reviewer.id)
    assert locked["locked_until"] is not None

    unlocked = client.post(f"/api/users/{reviewer.id}/unlock", headers=as_admin)

    assert unlocked.status_code == 200
    assert unlocked.json()["locked_until"] is None
    assert login(client, "reviewer@example.com", PASSWORD).status_code == 200
    # Failures after the unlock count again.
    for _ in range(5):
        login(client, "reviewer@example.com", "wrong-password")
    assert login(client, "reviewer@example.com", PASSWORD).status_code == 429


def test_a_reset_link_also_lifts_the_lock(
    client: TestClient, reviewer: User, as_admin: dict[str, str]
) -> None:
    for _ in range(5):
        login(client, "reviewer@example.com", "wrong-password")
    reset(client, make_link(client, reviewer, as_admin))
    assert login(client, "reviewer@example.com", "brand-new-password").status_code == 200


def test_wrong_current_passwords_count_toward_the_lock(
    client: TestClient, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    # A stolen session can't be used to guess the password without limit.
    for _ in range(5):
        wrong = client.post(
            "/api/auth/password",
            headers=as_reviewer,
            json={"current_password": "guess-guess", "new_password": "whatever-123"},
        )
        assert wrong.status_code == 400
    blocked = client.post(
        "/api/auth/password",
        headers=as_reviewer,
        json={"current_password": PASSWORD, "new_password": "whatever-123"},
    )
    assert blocked.status_code == 429


def test_the_demo_accounts_password_cant_be_reset(db: Session, reviewer: User) -> None:
    with pytest.raises(ResetLinkRefused, match="demo account"):
        create_reset_link(
            db, reviewer, datetime.now(UTC), by=None, demo_email=" Reviewer@example.com"
        )
