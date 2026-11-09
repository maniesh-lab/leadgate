"""Two-factor login: turning it on and off, the second login step, and the admin rule."""

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.security import create_two_factor_token
from app.db.models import User
from app.main import create_app
from tests.conftest import PASSWORD, TEST_JWT_SECRET, auth_headers


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def code(secret: str, seconds: int = 0) -> str:
    """The authenticator code `seconds` from now (30 = the next one)."""
    return pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=seconds))


def turn_on(client: TestClient, headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Two-factor on for this user: the secret, and the fresh token the API hands back."""
    setup = client.post("/api/auth/two-factor/setup", headers=headers, json={"password": PASSWORD})
    assert setup.status_code == 200
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/leadgate:")
    assert body["qr_svg"].startswith("data:image/svg+xml")
    on = client.post(
        "/api/auth/two-factor/enable", headers=headers, json={"code": code(body["secret"])}
    )
    assert on.status_code == 200
    return body["secret"], {"Authorization": f"Bearer {on.json()['access_token']}"}


def test_turning_it_on_needs_the_password_and_a_code(
    client: TestClient, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    wrong = client.post(
        "/api/auth/two-factor/setup", headers=as_reviewer, json={"password": "nope-nope"}
    )
    assert wrong.status_code == 400
    setup = client.post(
        "/api/auth/two-factor/setup", headers=as_reviewer, json={"password": PASSWORD}
    ).json()
    bad = client.post("/api/auth/two-factor/enable", headers=as_reviewer, json={"code": "000000"})
    assert bad.status_code == 400

    secret = setup["secret"]
    on = client.post(
        "/api/auth/two-factor/enable", headers=as_reviewer, json={"code": code(secret)}
    )

    assert on.status_code == 200
    # Turning it on ends the other logins; the answer's token is this browser's new one.
    assert client.get("/api/auth/me", headers=as_reviewer).status_code == 401
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {on.json()['access_token']}"}
    )
    assert me.json()["two_factor_enabled"] is True


def test_login_then_asks_for_a_code_and_each_code_works_once(
    client: TestClient, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    secret, _ = turn_on(client, as_reviewer)

    first = login(client, "reviewer@example.com").json()
    assert first["two_factor_required"] is True
    assert first["access_token"] is None
    step_token = first["two_factor_token"]
    # The half-way token isn't a login.
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {step_token}"}).status_code
        == 401
    )

    # The code used to turn it on can't be used again; the next one works, once.
    reused = client.post(
        "/api/auth/login/two-factor", json={"two_factor_token": step_token, "code": code(secret)}
    )
    assert reused.status_code == 401
    next_code = code(secret, 30)
    ok = client.post(
        "/api/auth/login/two-factor", json={"two_factor_token": step_token, "code": next_code}
    )
    assert ok.status_code == 200
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}
    )
    assert me.status_code == 200
    again = client.post(
        "/api/auth/login/two-factor", json={"two_factor_token": step_token, "code": next_code}
    )
    assert again.status_code == 401


def test_wrong_codes_count_toward_the_login_lock(
    client: TestClient, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    secret, _ = turn_on(client, as_reviewer)
    step_token = login(client, "reviewer@example.com").json()["two_factor_token"]
    for _ in range(5):
        wrong = client.post(
            "/api/auth/login/two-factor", json={"two_factor_token": step_token, "code": "123456"}
        )
        assert wrong.status_code == 401
    right = client.post(
        "/api/auth/login/two-factor",
        json={"two_factor_token": step_token, "code": code(secret, 30)},
    )
    assert right.status_code == 429


def test_an_old_or_forged_step_token_is_refused(
    client: TestClient, db: Session, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    secret, _ = turn_on(client, as_reviewer)
    # Made before a password change (older token version), or with another key.
    stale = create_two_factor_token(reviewer.id, TEST_JWT_SECRET, datetime.now(UTC), 0)
    forged = create_two_factor_token(reviewer.id, "another-key-" + "y" * 32, datetime.now(UTC), 1)
    for token in (stale, forged, "not-a-token", auth_headers(reviewer)["Authorization"][7:]):
        answer = client.post(
            "/api/auth/login/two-factor",
            json={"two_factor_token": token, "code": code(secret, 30)},
        )
        assert answer.status_code == 401, token


def test_turning_it_off_needs_the_password_and_a_code(
    client: TestClient, reviewer: User, as_reviewer: dict[str, str]
) -> None:
    secret, headers = turn_on(client, as_reviewer)
    wrong = client.post(
        "/api/auth/two-factor/disable",
        headers=headers,
        json={"password": PASSWORD, "code": "000000"},
    )
    assert wrong.status_code == 400
    off = client.post(
        "/api/auth/two-factor/disable",
        headers=headers,
        json={"password": PASSWORD, "code": code(secret, 30)},
    )
    assert off.status_code == 200
    assert login(client, "reviewer@example.com").json()["access_token"]


def test_an_admin_turns_off_someone_elses_two_factor(
    client: TestClient,
    admin: User,
    reviewer: User,
    as_admin: dict[str, str],
    as_reviewer: dict[str, str],
) -> None:
    _, reviewer_headers = turn_on(client, as_reviewer)
    assert client.post(f"/api/users/{admin.id}/two-factor/off", headers=as_admin).status_code == 409

    off = client.post(f"/api/users/{reviewer.id}/two-factor/off", headers=as_admin)

    assert off.status_code == 200
    assert off.json()["two_factor_enabled"] is False
    assert client.get("/api/auth/me", headers=reviewer_headers).status_code == 401  # logins end
    assert login(client, "reviewer@example.com").json()["access_token"]


def test_with_the_rule_on_an_admin_needs_two_factor_for_admin_tools(
    settings, admin: User, as_admin: dict[str, str]
) -> None:
    strict = settings.model_copy(update={"require_admin_two_factor": True})
    with TestClient(create_app(strict)) as client:
        me = client.get("/api/auth/me", headers=as_admin).json()
        assert me["two_factor_needed"] is True
        blocked = client.get("/api/users", headers=as_admin)
        assert blocked.status_code == 403
        assert "two-factor" in blocked.json()["error"]["message"]

        # Nor the admin-only audit rows (logins, accounts): it sees what a reviewer sees.
        login(client, "nobody@example.com", "wrong-password")
        rows = client.get("/api/audit", headers=as_admin).json()["items"]
        assert not [row for row in rows if row["action"].startswith("auth.")]

        _, headers = turn_on(client, as_admin)  # the Account page still works

        rows = client.get("/api/audit", headers=headers).json()["items"]
        assert [row for row in rows if row["action"] == "auth.login_failed"]

        assert client.get("/api/users", headers=headers).status_code == 200
        assert client.get("/api/auth/me", headers=headers).json()["two_factor_needed"] is False
