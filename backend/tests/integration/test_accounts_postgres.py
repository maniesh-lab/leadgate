"""On Postgres, password checks for one email wait for each other (an advisory lock), and a
waiter gives up instead of holding a connection (`app.services.accounts`)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

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
