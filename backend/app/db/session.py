"""Engine creation and a simple DB reachability check.

`create_app()` makes one engine at startup and keeps it on `app.state.engine`;
`/ready` calls `db_is_reachable()`. Phase 2 adds sessions and models here.
Works with Postgres (real) and SQLite (unit tests).
"""

import structlog
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

log = structlog.get_logger(__name__)


def make_engine(database_url: str) -> Engine:
    # pool_pre_ping swaps out dead connections (e.g. after Postgres restarts) instead of failing.
    return create_engine(database_url, pool_pre_ping=True)


def db_is_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.warning("db_unreachable", error=str(exc))
        return False
    return True
