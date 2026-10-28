"""The Alembic migration on SQLite: builds exactly the modelled tables and undoes cleanly."""

from datetime import UTC
from pathlib import Path

from alembic import command

from app.db.base import Base
from app.db.models import User, UserRole
from app.db.session import make_engine, make_session_factory
from tests.migrations import alembic_config, schema_diff, table_names


def test_upgrade_matches_models_and_downgrade_removes_everything(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = alembic_config(url)
    engine = make_engine(url)

    command.upgrade(config, "head")
    assert table_names(engine) == set(Base.metadata.tables) | {"alembic_version"}
    assert schema_diff(engine) == []

    command.downgrade(config, "base")
    assert table_names(engine) == {"alembic_version"}
    engine.dispose()


def test_migrated_db_fills_in_timestamp_defaults(tmp_path: Path) -> None:
    # schema_diff ignores server defaults, so check one works: SQLite has no now().
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)

    with make_session_factory(engine)() as session:
        user = User(email="a@example.com", password_hash="x", role=UserRole.ADMIN)
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.created_at.tzinfo is UTC
    engine.dispose()
