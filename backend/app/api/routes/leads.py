"""Leads: `GET /api/leads` (filter, sort, search, pages), `GET /api/leads/{id}`, and the
opt-out `DELETE /api/leads/{id}/data` (admins).

Filters (all optional, combined with AND): `status`, `campaign_id` (the lead was in one
of the campaign's runs), `area`, `min_score` / `max_score`, `has_email`, `flag` (a
run item of the lead carries that flag, e.g. `possible_duplicate`) and `q` (part of the
name). Flags live on run items, not leads, so `campaign_id` and `flag` look there.

The opt-out wipes the lead's contact data (`app.services.privacy`), writes an audit row,
and rejects the lead's items still waiting at the approval gate: their checkpoints hold
the old contact data, so they must never be approved (see `app.services.approvals`).
"""

from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import ColumnElement, Select, Text, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, get_or_404, paginate
from app.api.errors import error_docs
from app.api.routes.runs import reviews_for
from app.db.models import (
    ActorType,
    Approval,
    Campaign,
    Lead,
    LeadStatus,
    Run,
    RunItem,
    RunItemStatus,
    User,
)
from app.schemas.common import Page, Review
from app.schemas.leads import LeadDetail, LeadRow, LeadRunItem
from app.services.approvals import NotAwaitingDecision, decide
from app.services.audit import record
from app.services.privacy import opt_out_lead

router = APIRouter(prefix="/leads", tags=["leads"], responses=error_docs(401))

Sort = Literal["-first_seen_at", "first_seen_at", "-score", "score", "name", "-name", "-updated_at"]
SORTS = {
    "-first_seen_at": Lead.first_seen_at.desc(),
    "first_seen_at": Lead.first_seen_at.asc(),
    # Leads without a score go last either way.
    "-score": Lead.score.desc().nulls_last(),
    "score": Lead.score.asc().nulls_last(),
    "name": Lead.name.asc(),
    "-name": Lead.name.desc(),
    "-updated_at": Lead.updated_at.desc(),
}
OPT_OUT_REASON = "the lead opted out"


@router.get("")
def list_leads(
    _user: CurrentUser,
    db: DbSession,
    paging: Paging,
    status: LeadStatus | None = None,
    campaign_id: Annotated[int | None, Query(description="leads in this campaign's runs")] = None,
    area: Annotated[str | None, Query(description="exact area, any case")] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    max_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    has_email: Annotated[bool | None, Query(description="true: with an email")] = None,
    flag: Annotated[
        str | None, Query(pattern=r"^[a-z_]+$", description="e.g. possible_duplicate")
    ] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100, description="in the name")] = None,
    sort: Sort = "-first_seen_at",
) -> Page[LeadRow]:
    """Leads, newest first unless `sort` says otherwise (`-` = descending)."""
    stmt = leads_query(
        status=status,
        campaign_id=campaign_id,
        area=area,
        min_score=min_score,
        max_score=max_score,
        has_email=has_email,
        flag=flag,
        q=q,
        sort=sort,
    )
    rows, total = paginate(db, stmt, paging)
    return Page(
        items=[LeadRow.model_validate(lead) for (lead,) in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


def leads_query(
    *,
    status: LeadStatus | None = None,
    campaign_id: int | None = None,
    area: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    has_email: bool | None = None,
    flag: str | None = None,
    q: str | None = None,
    sort: Sort = "-first_seen_at",
) -> Select[tuple[Lead]]:
    """The lead list's query (also measured by `scripts/sql_explain.py`)."""
    stmt = select(Lead)
    if status is not None:
        stmt = stmt.where(Lead.status == status)
    if campaign_id is not None:
        stmt = stmt.where(
            exists(
                select(RunItem.id)
                .join(Run, Run.id == RunItem.run_id)
                .where(RunItem.lead_id == Lead.id, Run.campaign_id == campaign_id)
            )
        )
    if area is not None:
        stmt = stmt.where(func.lower(Lead.area) == area.strip().lower())
    if min_score is not None:
        stmt = stmt.where(Lead.score >= min_score)
    if max_score is not None:
        stmt = stmt.where(Lead.score <= max_score)
    if has_email is not None:
        stmt = stmt.where(Lead.email.is_not(None) if has_email else Lead.email.is_(None))
    if flag is not None:
        stmt = stmt.where(
            exists(select(RunItem.id).where(RunItem.lead_id == Lead.id, has_flag(flag)))
        )
    if q is not None:
        stmt = stmt.where(Lead.name.ilike(f"%{escape_like(q.strip())}%", escape="\\"))
    return stmt.order_by(SORTS[sort], Lead.id)


@router.get("/{lead_id}", responses=error_docs(404))
def get_lead(lead_id: int, _user: CurrentUser, db: DbSession) -> LeadDetail:
    """Everything about a lead: its fields, website findings, score breakdown and runs."""
    lead = get_or_404(db, Lead, lead_id, "lead")
    history = lead_history(db, lead.id)
    review = reviews_for(db, [history[0].run_item_id])[history[0].run_item_id] if history else None
    enrichment = lead.enrichment
    if enrichment is not None:
        enrichment = {key: value for key, value in enrichment.items() if key != "text"}
    return LeadDetail(
        **LeadRow.model_validate(lead).model_dump(),
        source_ref=lead.source_ref,
        services=lead.services or [],
        established_year=lead.established_year,
        verified=lead.verified,
        business_status=lead.business_status,
        lat=lead.lat,
        lng=lead.lng,
        crm_record_id=lead.crm_record_id,
        enrichment=enrichment,
        review=review or Review(),
        history=history,
    )


@router.delete(
    "/{lead_id}/data", status_code=status.HTTP_204_NO_CONTENT, responses=error_docs(403, 404)
)
def delete_lead_data(lead_id: int, admin: AdminUser, db: DbSession) -> Response:
    """Opt the lead out: wipe its contact data and website findings, never collect or crawl
    it again, and reject its items waiting for approval. Safe to repeat."""
    lead = get_or_404(db, Lead, lead_id, "lead")
    opt_out_lead(db, lead)
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="lead.opted_out",
        entity_type="lead",
        entity_id=lead.id,
    )
    db.commit()
    waiting = db.scalars(
        select(RunItem.id).where(
            RunItem.lead_id == lead.id, RunItem.status == RunItemStatus.AWAITING_APPROVAL
        )
    ).all()
    for item_id in waiting:
        # A reviewer may have decided it a moment ago; the opt-out still stands.
        with suppress(NotAwaitingDecision):
            decide(db, item_id, admin, "rejected", OPT_OUT_REASON, datetime.now(UTC))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def lead_history(db: Session, lead_id: int) -> list[LeadRunItem]:
    rows = db.execute(lead_history_query(lead_id)).all()
    return [
        LeadRunItem(
            run_item_id=item.id,
            run_id=item.run_id,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            status=item.status,
            route=item.route,
            score=item.score,
            flags=item.flags,
            decision=approval.decision if approval else None,
            reason=approval.reason if approval else None,
            reviewer_email=email,
            created_at=item.created_at,
        )
        for item, campaign_id, campaign_name, approval, email in rows
    ]


def lead_history_query(lead_id: int) -> Select[tuple[RunItem, int, str, Approval, str]]:
    """Every run item of the lead, newest first, with its campaign and decision."""
    return (
        select(RunItem, Run.campaign_id, Campaign.name, Approval, User.email)
        .join(Run, Run.id == RunItem.run_id)
        .join(Campaign, Campaign.id == Run.campaign_id)
        .outerjoin(Approval, Approval.run_item_id == RunItem.id)
        .outerjoin(User, User.id == Approval.reviewer_id)
        .where(RunItem.lead_id == lead_id)
        .order_by(RunItem.created_at.desc(), RunItem.id.desc())
    )


def has_flag(flag: str) -> ColumnElement[bool]:
    """The run item's flags hold `flag` or `flag:<something>` (e.g. possible_duplicate:12).

    The JSON list is matched as text, which works the same on SQLite and Postgres
    (`["a", "b"]`): look for `"flag"` or `"flag:`.
    """
    flags = cast(RunItem.flags, Text)
    name = escape_like(flag)
    return or_(
        flags.like(f'%"{name}"%', escape="\\"),
        flags.like(f'%"{name}:%', escape="\\"),
    )


def escape_like(text: str) -> str:
    """`%` and `_` mean "anything" in LIKE; escape them so they match themselves."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
