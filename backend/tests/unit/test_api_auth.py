"""Tests for login, `/auth/me`, roles, `/users` and the shared error shape."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import create_access_token
from app.db.models import AuditLog, User, UserRole
from app.main import create_app
from tests.conftest import PASSWORD, TEST_JWT_SECRET, add_user


def login(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def test_login_returns_a_token_that_works(client: TestClient, admin: User) -> None:
    response = login(client, "Admin@Example.com ", PASSWORD)  # case and spaces don't matter
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"
    assert me.json()["role"] == "admin"


def test_wrong_password_and_unknown_email_get_the_same_answer(
    client: TestClient, admin: User
) -> None:
    wrong = login(client, "admin@example.com", "nope-nope")
    unknown = login(client, "nobody@example.com", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"] == {
        "code": "unauthorized",
        "message": "wrong email or password",
        "details": None,
    }
    assert wrong.headers["WWW-Authenticate"] == "Bearer"


def test_deactivated_user_cant_log_in_or_use_an_old_token(
    client: TestClient, db: Session, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    reviewer.is_active = False
    db.commit()
    assert login(client, "reviewer@example.com", PASSWORD).status_code == 401
    assert client.get("/api/auth/me", headers=as_reviewer).status_code == 401


def test_me_needs_a_valid_token(client: TestClient, admin: User) -> None:
    assert client.get("/api/auth/me").status_code == 401
    bad = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401
    assert bad.json()["error"]["message"] == "invalid login token"

    old = create_access_token(admin.id, TEST_JWT_SECRET, 60, datetime.now(UTC) - timedelta(hours=2))
    expired = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})
    assert expired.status_code == 401
    assert "expired" in expired.json()["error"]["message"]


def test_token_of_a_deleted_user_is_refused(client: TestClient, db: Session) -> None:
    token = create_access_token(999, TEST_JWT_SECRET, 60, datetime.now(UTC))
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_admin_creates_a_reviewer_who_can_log_in(
    client: TestClient, db: Session, as_admin: dict[str, str]
) -> None:
    body = {"email": " New@Example.com", "password": "long-enough"}
    response = client.post("/api/users", json=body, headers=as_admin)
    assert response.status_code == 201
    created = response.json()
    assert (created["email"], created["role"], created["is_active"]) == (
        "new@example.com",
        "reviewer",
        True,
    )
    assert "password" not in str(created)
    assert login(client, "new@example.com", "long-enough").status_code == 200

    audit = db.scalars(select(AuditLog).where(AuditLog.action == "user.created")).one()
    assert (audit.actor, audit.entity_id) == ("admin@example.com", created["id"])


def test_creating_a_taken_email_is_a_conflict(
    client: TestClient, admin: User, as_admin: dict[str, str]
) -> None:
    body = {"email": "ADMIN@example.com", "password": "long-enough"}
    response = client.post("/api/users", json=body, headers=as_admin)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_bad_user_fields_are_a_validation_error_without_the_password(
    client: TestClient, as_admin: dict[str, str]
) -> None:
    body = {"email": "not-an-email", "password": "pw-4z", "role": "boss"}
    response = client.post("/api/users", json=body, headers=as_admin)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert {tuple(detail["loc"]) for detail in error["details"]} == {
        ("body", "email"),
        ("body", "password"),
        ("body", "role"),
    }
    assert "pw-4z" not in response.text  # the input is never echoed


def test_reviewers_cant_manage_users(client: TestClient, as_reviewer: dict[str, str]) -> None:
    assert client.get("/api/users", headers=as_reviewer).status_code == 403
    response = client.post(
        "/api/users",
        json={"email": "x@example.com", "password": "long-enough"},
        headers=as_reviewer,
    )
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "forbidden",
        "message": "only an admin can do this",
        "details": None,
    }


def test_users_list_is_paginated(client: TestClient, db: Session, as_admin) -> None:
    for number in range(3):
        add_user(db, f"r{number}@example.com", UserRole.REVIEWER)

    response = client.get("/api/users?page=2&page_size=3", headers=as_admin)
    body = response.json()
    assert (body["total"], body["page"], body["page_size"]) == (4, 2, 3)
    assert [user["email"] for user in body["items"]] == ["r2@example.com"]

    assert client.get("/api/users?page=0", headers=as_admin).status_code == 422
    assert client.get("/api/users?page_size=201", headers=as_admin).status_code == 422


def test_unknown_path_uses_the_error_shape(client: TestClient) -> None:
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": None}
    }


def test_a_crash_uses_the_error_shape_without_details(settings) -> None:
    app = create_app(settings)

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "something went wrong on our side",
            "details": None,
        }
    }
    assert "secret internals" not in response.text


# --- Slowing down password guessing ---


def test_a_failed_login_leaves_an_audit_row(client: TestClient, db: Session, admin: User) -> None:
    login(client, "admin@example.com", "nope-nope")
    row = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).one()
    assert (row.actor, row.entity_type, row.entity_id) == ("admin@example.com", "user", admin.id)
    assert row.details == {"ip": "testclient"}


def test_five_failures_lock_the_email_for_15_minutes(
    client: TestClient, db: Session, admin: User
) -> None:
    for _ in range(5):
        assert login(client, "admin@example.com", "nope-nope").status_code == 401

    # Even the right password waits now: otherwise the guessing could go on.
    locked = login(client, "admin@example.com", PASSWORD)
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_requests"
    assert locked.headers["Retry-After"] == "900"

    # Another email from the same address isn't locked yet (its limit is 20).
    add_user(db, "other@example.com", UserRole.REVIEWER)
    assert login(client, "other@example.com", PASSWORD).status_code == 200


def test_old_failures_dont_count(client: TestClient, db: Session, admin: User) -> None:
    for _ in range(5):
        login(client, "admin@example.com", "nope-nope")
    for row in db.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")):
        row.occurred_at = datetime.now(UTC) - timedelta(minutes=16)
    db.commit()

    assert login(client, "admin@example.com", PASSWORD).status_code == 200


def test_twenty_failures_from_one_address_lock_every_email(client: TestClient, admin: User) -> None:
    for n in range(20):
        login(client, f"guess{n}@example.com", "nope-nope")  # 1 try each: no email limit

    assert login(client, "admin@example.com", PASSWORD).status_code == 429
