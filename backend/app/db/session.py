"""Engine and session creation, plus a simple DB reachability check.

`create_app()` makes one engine at startup and keeps it on `app.state.engine`;
`/ready` calls `db_is_reachable()`. The CLI (and later the API routes and the
worker) open sessions with `make_session_factory()`.
Works with Postgres (real) and SQLite (unit tests).
"""

from typing import Any

import structlog
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

log = structlog.get_logger(__name__)


def make_engine(database_url: str) -> Engine:
    # pool_pre_ping swaps out dead connections (e.g. after Postgres restarts) instead of failing.
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # SQLite ignores foreign keys unless each connection turns them on.
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after commit (e.g. to return them).
    return sessionmaker(engine, expire_on_commit=False)


def db_is_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.warning("db_unreachable", error=str(exc))
        return False
    return True
