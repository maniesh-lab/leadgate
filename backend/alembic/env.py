"""Alembic's entry point: connects to the DB and runs the migration scripts.

Alembic runs this file for every command (`upgrade`, `downgrade`, `revision
--autogenerate`). The URL comes from `sqlalchemy.url` if a caller set it (the
migration tests do), otherwise from `DATABASE_URL` via `app.config`.

LangGraph's checkpoint tables are created by the checkpointer's own `.setup()`,
not by Alembic, so `include_object` hides them from autogenerate; otherwise
Alembic would try to drop them.
"""

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.models  # noqa: F401  (importing it registers every table on Base.metadata)
from app.config import get_settings
from app.db.base import Base, UTCDateTime

config = context.config

# disable_existing_loggers=False keeps the app's own loggers working when tests call Alembic.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

LANGGRAPH_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "table" and name in LANGGRAPH_TABLES)


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    # Write our UTCDateTime as a plain timestamptz, so migrations never import app code.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False  # anything else: Alembic's default rendering


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_item=render_item,
            # SQLite can't ALTER most things; batch mode rebuilds the table instead.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
