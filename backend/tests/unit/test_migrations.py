"""The Alembic migration on SQLite: builds exactly the modelled tables and undoes cleanly."""

from pathlib import Path

from alembic import command

from app.db.base import Base
from app.db.session import make_engine
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
