"""The audit trail, read-only: `GET /api/audit`, newest first, with filters.

Rows come from `app.services.audit.record()` (nodes, the worker, reviewers, admins); the
API never changes them, and on Postgres a trigger refuses UPDATE and DELETE anyway.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, Paging, paginate
from app.api.errors import error_docs
from app.db.models import AuditLog
from app.schemas.audit import AuditOut
from app.schemas.common import Page

router = APIRouter(prefix="/audit", tags=["audit"], responses=error_docs(401))


@router.get("")
def list_audit(
    _user: CurrentUser,
    db: DbSession,
    paging: Paging,
    run_id: int | None = None,
    entity_type: Annotated[str | None, Query(description="e.g. lead, run, run_item")] = None,
    entity_id: int | None = None,
    actor: Annotated[str | None, Query(description="an email, a node name or worker")] = None,
    action: Annotated[str | None, Query(description="e.g. approval.approved")] = None,
    occurred_from: Annotated[
        datetime | None, Query(alias="from", description="on or after (ISO 8601)")
    ] = None,
    occurred_to: Annotated[datetime | None, Query(alias="to", description="before")] = None,
) -> Page[AuditOut]:
    """Audit rows, newest first."""
    stmt = select(AuditLog).order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
    filters = [
        (AuditLog.run_id, run_id),
        (AuditLog.entity_type, entity_type),
        (AuditLog.entity_id, entity_id),
        (AuditLog.actor, actor),
        (AuditLog.action, action),
    ]
    for column, value in filters:
        if value is not None:
            stmt = stmt.where(column == value)
    if occurred_from is not None:
        stmt = stmt.where(AuditLog.occurred_at >= as_utc(occurred_from))
    if occurred_to is not None:
        stmt = stmt.where(AuditLog.occurred_at < as_utc(occurred_to))
    rows, total = paginate(db, stmt, paging)
    return Page(
        items=[AuditOut.model_validate(row) for (row,) in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


def as_utc(moment: datetime) -> datetime:
    """A time sent without a timezone (`2026-10-30T12:00`) is read as UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
