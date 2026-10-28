"""FastAPI app factory.

`create_app()` builds the app: logging, the DB engine, middleware and routers.
Uvicorn calls it with `--factory` (see the Dockerfile); tests call it directly
with their own `Settings`, so nothing is created at import time.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health
from app.config import Settings, get_settings
from app.db.session import make_engine
from app.observability.correlation import correlation_id_middleware
from app.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = make_engine(settings.database_url)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="leadgate", version="0.1.0", lifespan=lifespan)
    app.middleware("http")(correlation_id_middleware)
    app.include_router(health.router, prefix="/api")
    return app
