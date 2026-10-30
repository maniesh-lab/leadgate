"""One JSON shape for every API error.

Every error the API returns looks like this:

    {"error": {"code": "not_found", "message": "campaign 7 not found", "details": null}}

`code` is a short, stable name the frontend can check ("unauthorized", "forbidden",
"not_found", "conflict", "validation_error", ...); `message` is for people; `details`
lists the bad fields for a validation error (422). Routes just raise FastAPI's
`HTTPException`; the handlers here turn it (and validation errors, and crashes) into
this shape. `create_app()` registers them with `add_error_handlers()`.
"""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorBody(BaseModel):
    """The body of every error response (used in the OpenAPI docs)."""

    error: ErrorInfo


def error_response(
    status: int,
    message: str,
    *,
    code: str | None = None,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorBody(
        error=ErrorInfo(code=code or code_for(status), message=message, details=details)
    )
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


def code_for(status: int) -> str:
    """404 -> "not_found", 409 -> "conflict", ... (from the standard status names)."""
    if status == 422:
        return "validation_error"  # the same code as FastAPI's own field checks
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "error"


async def http_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return error_response(exc.status_code, str(exc.detail), headers=exc.headers)


async def validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only where and what: the raw `input` could echo a password back.
    details = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return error_response(
        422, "the request has invalid fields", code="validation_error", details=details
    )


async def server_error(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error=f"{type(exc).__name__}: {exc}")
    return error_response(500, "something went wrong on our side", code="internal_error")


def add_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, server_error)


def error_docs(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The `responses=` entry that shows these error codes in the OpenAPI docs."""
    return {
        status: {"model": ErrorBody, "description": HTTPStatus(status).phrase}
        for status in statuses
    }
