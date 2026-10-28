"""Helpers the migration tests share (SQLite unit tests and the Postgres integration test)."""

from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # alembic.ini is read with configparser, which treats "%" as special.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def schema_diff(engine: Engine) -> list[Any]:
    """What autogenerate would change to make the DB match the models. Empty = in sync."""
    with engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())
