"""Fixtures for tests that need a real Postgres.

They only run when `TEST_POSTGRES_URL` points at a Postgres server whose user may
create databases (the compose `db` user can), e.g.
`postgresql+psycopg://leadgate:leadgate@localhost:5432/leadgate`. Otherwise they
are skipped, so `pytest` alone never needs Postgres.

Each test gets its own throwaway database, created and dropped here, so these
tests never touch the data in the database the URL names.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, make_url, text


@pytest.fixture
def postgres_url() -> Iterator[str]:
    server_url = os.environ.get("TEST_POSTGRES_URL")
    if not server_url:
        pytest.skip("set TEST_POSTGRES_URL to run the Postgres tests")

    name = f"leadgate_test_{uuid.uuid4().hex[:8]}"
    # CREATE/DROP DATABASE can't run inside a transaction, hence AUTOCOMMIT.
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    yield make_url(server_url).set(database=name).render_as_string(hide_password=False)

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()
