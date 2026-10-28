"""Liveness and readiness checks, used by Docker healthchecks and hosting platforms.

- `/api/health`: the process is up (never touches the DB).
- `/api/ready`: the process can do real work, i.e. the DB answers. 503 if not.
"""

from fastapi import APIRouter, Request, Response, status

from app.db.session import db_is_reachable

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict[str, str]:
    if db_is_reachable(request.app.state.engine):
        return {"status": "ok", "db": "ok"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "db": "unreachable"}
