"""The Alembic migration on real Postgres, including the Postgres-only extras.

Skipped unless `TEST_POSTGRES_URL` is set (see `conftest.py` in this folder).
"""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.base import Base
from app.db.session import make_engine
from tests.migrations import alembic_config, schema_diff, table_names


def test_upgrade_and_downgrade_on_postgres(postgres_url: str) -> None:
    config = alembic_config(postgres_url)
    engine = make_engine(postgres_url)

    command.upgrade(config, "head")
    assert table_names(engine) == set(Base.metadata.tables) | {"alembic_version"}
    assert schema_diff(engine) == []
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")) == 1

    command.downgrade(config, "base")
    assert table_names(engine) == {"alembic_version"}
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")) == 0
    engine.dispose()


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET actor = 'someone else'", "DELETE FROM audit_log"]
)
def test_audit_log_is_append_only(postgres_url: str, statement: str) -> None:
    command.upgrade(alembic_config(postgres_url), "head")
    engine = make_engine(postgres_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (actor_type, actor, action, entity_type, details) "
                "VALUES ('system', 'test', 'run.started', 'run', '{}')"
            )
        )

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text(statement))

    with engine.connect() as conn:
        assert conn.scalar(text("SELECT actor FROM audit_log")) == "test"
    engine.dispose()
