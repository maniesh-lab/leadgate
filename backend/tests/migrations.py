"""Helpers the migration tests share (SQLite unit tests and the Postgres integration test)."""

from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from app.db.base import Base
from app.db.models import POSTGRES_ONLY_INDEXES

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # alembic.ini is read with configparser, which treats "%" as special.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def schema_diff(engine: Engine) -> list[Any]:
    """What autogenerate would change to make the DB match the models. Empty = in sync.

    Postgres-only indexes are left out on SQLite, like env.py's `include_object` does.
    """

    def include_object(obj: Any, name: str | None, type_: str, *_: Any) -> bool:
        return not (type_ == "index" and name in POSTGRES_ONLY_INDEXES and not postgres)

    postgres = engine.dialect.name == "postgresql"
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object": include_object})
        return compare_metadata(context, Base.metadata)


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())
