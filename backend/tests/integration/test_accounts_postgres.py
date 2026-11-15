"""On Postgres, password checks for one email wait for each other (an advisory lock), and a
waiter gives up instead of holding a connection (`app.services.accounts`)."""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.auth import serialize
from app.services import accounts


def test_a_second_check_for_the_same_email_waits_then_gives_up(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(accounts, "ATTEMPT_WAIT", "200ms")
    engine = create_engine(postgres_url)
    try:
        with Session(engine) as first, Session(engine) as second, Session(engine) as other:
            accounts.one_attempt_at_a_time(first, "ann@example.com")
            with pytest.raises(accounts.Busy):
                accounts.one_attempt_at_a_time(second, "ann@example.com")
            accounts.one_attempt_at_a_time(other, "bob@example.com")  # another email: no wait
            first.rollback()  # the lock ends with the transaction
            accounts.one_attempt_at_a_time(second, "ann@example.com")
    finally:
        engine.dispose()


def test_checks_from_one_address_wait_for_each_other_whatever_the_email(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Else a burst of guesses for different emails would all pass the per-address check
    # before any of their failures was saved.
    monkeypatch.setattr(accounts, "ATTEMPT_WAIT", "200ms")
    engine = create_engine(postgres_url)
    try:
        with Session(engine) as first, Session(engine) as second, Session(engine) as other:
            serialize(first, "ann@example.com", "203.0.113.9", lock_email=True)
            with pytest.raises(HTTPException) as busy:
                serialize(second, "bob@example.com", "203.0.113.9", lock_email=True)
            assert busy.value.status_code == 429
            serialize(other, "carl@example.com", "198.51.100.7", lock_email=True)  # no wait
    finally:
        engine.dispose()
