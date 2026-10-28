"""Correlation-ID middleware: one ID per request, on every log line and in the response.

Reads `X-Request-ID` from the request (n8n or the frontend may send one) or makes
a new UUID. The ID is bound into structlog's context so every log line during the
request carries it, and it is echoed back in the `X-Request-ID` response header.
Later phases pass it into job payloads, graph state and audit rows.

Registered by `create_app()`.
"""

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

HEADER = "X-Request-ID"

# Only accept short, plain IDs from clients so nobody can inject junk into our logs.
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

log = structlog.get_logger(__name__)


def pick_correlation_id(incoming: str | None) -> str:
    if incoming and _VALID_ID.match(incoming):
        return incoming
    return str(uuid.uuid4())


async def correlation_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    correlation_id = pick_correlation_id(request.headers.get(HEADER))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)

    response.headers[HEADER] = correlation_id
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    return response
