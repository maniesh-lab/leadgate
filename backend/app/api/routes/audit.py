"""The audit trail, read-only: `GET /api/audit`, newest first, with filters.

Rows come from `app.services.audit.record()` (nodes, the worker, reviewers, admins); the
API never changes them, and on Postgres a trigger refuses UPDATE and DELETE anyway.

A user row's `actor` (their email) shows as "user <id>" to anyone but an admin and that
user (`app.api.deps.Emails`). Some rows are for admins only, because the public demo's
reviewer login can read everything else here:
- login rows (`auth.*`, e.g. `auth.login_failed`): the email someone typed and their IP
- account rows (`user.*`): the users' emails
- the admin tools' rows: campaigns (`campaign.*`, what an admin searched for), CSV uploads
  (`csv.*`, the file name) and exports (`leads.exported`)
"""

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    Emails,
    Paging,
    ShownEmails,
    needs_two_factor,
    paginate,
)
from app.api.errors import error_docs
from app.db.models import ActorType, AuditLog, User, UserRole
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
    emails: ShownEmails,
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
    `user.*`, `campaign.*`, `csv.*`, `leads.exported`) are left out unless you're an admin.
    `actor` can be "user <id>" (how other users show to non-admins)."""
    actor = searchable_actor(db, actor, emails)
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
        items=shown_actors(db, [row for (row,) in rows], emails),
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


USER_LABEL = re.compile(r"^user (\d+)$")
NOBODY = "(no such user)"  # an actor no row has (no email, node or worker name looks so)
MAX_ID = 2**31 - 1  # Postgres integer ids


def searchable_actor(db: Session, actor: str | None, emails: Emails) -> str | None:
    """The `actor` filter as stored: "user <id>" becomes that user's email; another user's
    email from a non-admin matches nothing (it would tell them the email is a user's)."""
    if actor is None:
        return None
    if match := USER_LABEL.match(actor.strip()):
        user_id = int(match.group(1))
        user = db.get(User, user_id) if user_id <= MAX_ID else None
        return user.email if user else NOBODY
    if "@" in actor and not emails.may_search(actor):
        return NOBODY
    return actor


def shown_actors(db: Session, rows: list[AuditLog], emails: Emails) -> list[AuditOut]:
    """The rows as answers, each user actor's email shown as `emails` allows."""
    actors = {row.actor.lower() for row in rows if row.actor_type == ActorType.USER}
    stmt = select(func.lower(User.email), User.id).where(func.lower(User.email).in_(actors))
    ids: dict[str, int] = {email: user_id for email, user_id in db.execute(stmt)} if actors else {}
    shown = []
    for row in rows:
        out = AuditOut.model_validate(row)
        if row.actor_type == ActorType.USER:
            out.actor = emails.show(row.actor, ids.get(row.actor.lower())) or row.actor
        shown.append(out)
    return shown


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
