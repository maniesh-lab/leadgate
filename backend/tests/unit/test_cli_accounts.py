"""`python -m app.cli reset-password` and `unlock`: recovery for a locked-out admin."""

import re

import pytest
from fastapi.testclient import TestClient

from app.cli import main
from app.config import Settings
from app.db.models import User
from tests.conftest import PASSWORD


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def test_reset_password_prints_a_one_time_link_that_works(
    settings: Settings, client: TestClient, admin: User, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in range(5):
        login(client, "admin@example.com", "wrong-password")  # locked out

    assert main(["reset-password", "--email", "Admin@Example.com"], settings) == 0

    out = capsys.readouterr().out
    match = re.search(r"http://localhost:8080/reset-password#token=(\S+)", out)
    assert match, out
    reset = client.post(
        "/api/auth/reset-password",
        json={"token": match.group(1), "new_password": "a-new-password"},
    )
    assert reset.status_code == 204
    assert login(client, "admin@example.com", "a-new-password").status_code == 200


def test_reset_password_can_turn_two_factor_off(
    settings: Settings, db, admin: User, capsys: pytest.CaptureFixture[str]
) -> None:
    admin.totp_enabled = True
    admin.totp_secret = "JBSWY3DPEHPK3PXP"
    db.commit()

    args = [
        "reset-password",
        "--email",
        admin.email,
        "--two-factor-off",
        "--base-url",
        "https://x/",
    ]
    assert main(args, settings) == 0

    db.refresh(admin)
    assert admin.totp_enabled is False and admin.totp_secret is None
    assert "https://x/reset-password#token=" in capsys.readouterr().out


def test_unlock_lifts_the_lock(
    settings: Settings, client: TestClient, admin: User, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in range(5):
        login(client, "admin@example.com", "wrong-password")
    assert login(client, "admin@example.com").status_code == 429

    assert main(["unlock", "--email", "admin@example.com"], settings) == 0

    assert login(client, "admin@example.com").status_code == 200


def test_unknown_email(settings: Settings, db, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset-password", "--email", "nobody@example.com"], settings) == 1
    assert main(["unlock", "--email", "nobody@example.com"], settings) == 1
