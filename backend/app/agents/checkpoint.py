"""The checkpointer: where LangGraph saves each lead thread's state after every node.

`open_checkpointer(database_url)` gives LangGraph's `PostgresSaver` on the app's own
Postgres database, so a thread survives the worker being killed: the next worker reads
the last checkpoint and goes on from there. On startup it calls `.setup()`, which
creates (or migrates) LangGraph's own tables (`checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, `checkpoint_migrations`). Alembic leaves those tables alone (see
`include_object` in `alembic/env.py`).

The saver runs on a small psycopg connection pool that checks each connection before
handing it out, so a Postgres restart doesn't leave the worker with a dead connection.

For a SQLite URL (unit tests, quick local tries) it gives the in-memory saver instead:
fine for one process, but nothing survives a restart.

Used by `app.worker.main`.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import make_url

log = structlog.get_logger(__name__)


@contextmanager
def open_checkpointer(database_url: str) -> Iterator[BaseCheckpointSaver]:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        log.warning(
            "checkpointer_in_memory", reason="not a Postgres URL; nothing survives a restart"
        )
        yield InMemorySaver()
        return

    # psycopg wants a plain "postgresql://" URL, not SQLAlchemy's "postgresql+psycopg://".
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with ConnectionPool(
        conninfo,
        min_size=1,
        max_size=2,
        # The settings LangGraph's own `PostgresSaver.from_conn_string()` uses.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=ConnectionPool.check_connection,
    ) as pool:
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver
