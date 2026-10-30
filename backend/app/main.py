"""FastAPI app factory.

`create_app()` builds the app: logging, the DB engine + session factory, middleware,
the error handlers (one JSON error shape, see `app.api.errors`) and the routers.
Uvicorn calls it with `--factory` (see the Dockerfile); tests call it directly
with their own `Settings`, so nothing is created at import time. It refuses to build
the app without JWT_SECRET (no login could work).

With RUN_WORKER_IN_API=true the startup also starts the job worker in a background
thread (the same loop as `python -m app.worker`), and the shutdown stops it after its
current job.
"""

import threading
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI

from app.api.errors import add_error_handlers
from app.api.routes import approvals, auth, campaigns, health, leads, rubrics, runs, users
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.observability.correlation import correlation_id_middleware
from app.observability.logging import configure_logging
from app.worker.main import open_worker

# How long the shutdown waits for the in-API worker to finish its current job.
WORKER_STOP_SECONDS = 60


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.jwt_secret:
        # Fail at startup, not at the first login.
        raise RuntimeError("JWT_SECRET is not set; the API needs it to sign login tokens")
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = make_engine(settings.database_url)
        app.state.session_factory = make_session_factory(app.state.engine)
        with ExitStack() as stack:
            if settings.run_worker_in_api:
                worker = stack.enter_context(open_worker(settings))
                thread = threading.Thread(target=worker.run, name="worker", daemon=True)
                thread.start()
                stack.callback(thread.join, WORKER_STOP_SECONDS)
                stack.callback(worker.stop)  # runs first: callbacks run last-in, first-out
            yield
        app.state.engine.dispose()

    app = FastAPI(title="leadgate", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.middleware("http")(correlation_id_middleware)
    add_error_handlers(app)
    for module in (health, auth, users, rubrics, campaigns, runs, approvals, leads):
        app.include_router(module.router, prefix="/api")
    return app
