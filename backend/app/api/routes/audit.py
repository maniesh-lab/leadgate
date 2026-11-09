"""The audit trail, read-only: `GET /api/audit`, newest first, with filters.

Rows come from `app.services.audit.record()` (nodes, the worker, reviewers, admins); the
API never changes them, and on Postgres a trigger refuses UPDATE and DELETE anyway.

Some rows are for admins only, because the public demo's reviewer login can read
everything else here:
- login rows (`auth.*`, e.g. `auth.login_failed`): the email someone typed and their IP
- account rows (`user.*`): the users' emails
- the admin tools' rows: campaigns (`campaign.*`, what an admin searched for), CSV uploads
  (`csv.*`, the file name) and exports (`leads.exported`)
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import Select, select

from app.api.deps import AppSettings, CurrentUser, DbSession, Paging, needs_two_factor, paginate
from app.api.errors import error_docs
from app.db.models import AuditLog, UserRole
from app.schemas.audit import AuditOut
from app.schemas.common import Page

router = APIRouter(prefix="/audit", tags=["audit"], responses=error_docs(401))

# Actions only admins see (see the module docstring); each is matched as a prefix.
ADMIN_ONLY = ("auth.", "user.", "campaign.", "csv.", "leads.exported")


@router.get("")
def list_audit(
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
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
    """Audit rows, newest first. Login, account, campaign, upload and export rows (`auth.*`,
    `user.*`, `campaign.*`, `csv.*`, `leads.exported`) are left out unless you're an admin."""
    stmt = audit_query(
        run_id=run_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action=action,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    # An admin who must turn on two-factor first (REQUIRE_ADMIN_TWO_FACTOR) sees what a
    # reviewer sees until they do.
    sees_admin_rows = user.role == UserRole.ADMIN and not needs_two_factor(user, settings)
    if not sees_admin_rows:
        for prefix in ADMIN_ONLY:
            stmt = stmt.where(AuditLog.action.not_like(f"{prefix}%"))
    rows, total = paginate(db, stmt, paging)
    return Page(
        items=[AuditOut.model_validate(row) for (row,) in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


def audit_query(
    *,
    run_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor: str | None = None,
    action: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> Select[tuple[AuditLog]]:
    """The audit log's query (also measured by `scripts/sql_explain.py`)."""
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
    return stmt


def as_utc(moment: datetime) -> datetime:
    """A time sent without a timezone (`2026-10-30T12:00`) is read as UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
