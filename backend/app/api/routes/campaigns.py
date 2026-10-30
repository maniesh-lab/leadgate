"""Campaigns (`/api/campaigns`): anyone logged in reads them; admins create, edit and run them.

Starting a run (`POST /api/campaigns/{id}/runs`) only writes the run row and its
`start_run` job, in one transaction (`app.services.runs.create_run`); the worker does
the rest. The run keeps the request's correlation id, so its jobs, logs and audit rows
can be followed back to this request.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    AdminUser,
    CorrelationId,
    CurrentUser,
    DbSession,
    Paging,
    get_or_404,
    paginate,
)
from app.api.errors import error_docs
from app.db.models import ActorType, Campaign, ScoringRubric, User
from app.schemas.campaigns import CampaignCreate, CampaignOut, CampaignUpdate
from app.schemas.common import Page
from app.schemas.runs import RunOut
from app.services.audit import record
from app.services.runs import create_run
from app.sources.base import SourceError
from app.sources.osm import canonical_niche

router = APIRouter(prefix="/campaigns", tags=["campaigns"], responses=error_docs(401))


@router.get("")
def list_campaigns(
    _user: CurrentUser,
    db: DbSession,
    paging: Paging,
    archived: Annotated[bool, Query(description="list the archived campaigns instead")] = False,
) -> Page[CampaignOut]:
    """Campaigns, newest first."""
    stmt = (
        select(Campaign)
        .where(Campaign.is_archived.is_(archived))
        .order_by(Campaign.created_at.desc(), Campaign.id.desc())
    )
    rows, total = paginate(db, stmt, paging)
    return Page(
        items=[CampaignOut.model_validate(campaign) for (campaign,) in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=error_docs(403, 422))
def create_campaign(body: CampaignCreate, admin: AdminUser, db: DbSession) -> CampaignOut:
    campaign = Campaign(
        name=body.name,
        niche=body.niche,
        location=body.location,
        sources=body.sources,
        source_options=body.source_options.model_dump(exclude_none=True),
        rubric_id=body.rubric_id,
        created_by=admin.id,
    )
    check_campaign(db, campaign)
    db.add(campaign)
    db.flush()
    audit_campaign(db, admin, campaign, "campaign.created", body.model_dump(mode="json"))
    db.commit()
    return CampaignOut.model_validate(campaign)


@router.get("/{campaign_id}", responses=error_docs(404))
def get_campaign(campaign_id: int, _user: CurrentUser, db: DbSession) -> CampaignOut:
    return CampaignOut.model_validate(get_or_404(db, Campaign, campaign_id, "campaign"))


@router.patch("/{campaign_id}", responses=error_docs(403, 404, 422))
def update_campaign(
    campaign_id: int, body: CampaignUpdate, admin: AdminUser, db: DbSession
) -> CampaignOut:
    """Change some fields. Runs already started keep the rubric they started with."""
    campaign = get_or_404(db, Campaign, campaign_id, "campaign")
    changes = body.model_dump(mode="json", exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise unprocessable("send only the fields to change, without nulls")
    for field, value in changes.items():
        if field != "source_options":
            setattr(campaign, field, value)
    if "source_options" in changes:
        new_options = body.source_options.model_dump(exclude_none=True)
        # Keep OSM's cached geocode (the osm source checks it still matches the location).
        osm_area = (campaign.source_options or {}).get("osm_area")
        campaign.source_options = {**new_options, **({"osm_area": osm_area} if osm_area else {})}
    check_campaign(db, campaign)
    audit_campaign(db, admin, campaign, "campaign.updated", changes)
    db.commit()
    return CampaignOut.model_validate(campaign)


@router.post(
    "/{campaign_id}/runs", status_code=status.HTTP_201_CREATED, responses=error_docs(403, 404, 409)
)
def start_run(
    campaign_id: int, admin: AdminUser, db: DbSession, correlation_id: CorrelationId
) -> RunOut:
    """Queue a run: the worker collects the leads and takes each one to the approval gate."""
    campaign = get_or_404(db, Campaign, campaign_id, "campaign")
    if campaign.is_archived:
        raise HTTPException(status.HTTP_409_CONFLICT, f"campaign {campaign_id} is archived")
    run = create_run(
        db, campaign, datetime.now(UTC), triggered_by=admin, correlation_id=correlation_id
    )
    return RunOut.model_validate(run).model_copy(update={"campaign_name": campaign.name})


def check_campaign(db: Session, campaign: Campaign) -> None:
    """422 if the campaign couldn't run: unknown rubric, a niche OSM doesn't know, csv
    without a file."""
    # no_autoflush: don't save the unchecked changes (a bad rubric id would fail the FK).
    with db.no_autoflush:
        rubric = db.get(ScoringRubric, campaign.rubric_id)
    if rubric is None:
        raise unprocessable(f"rubric {campaign.rubric_id} not found")
    if "osm" in campaign.sources:
        try:
            canonical_niche(campaign.niche)
        except SourceError as exc:
            raise unprocessable(str(exc)) from exc
    if "csv" in campaign.sources and not (campaign.source_options or {}).get("csv_path"):
        raise unprocessable("the csv source needs source_options.csv_path")


def unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def audit_campaign(
    db: Session, admin: User, campaign: Campaign, action: str, details: dict
) -> None:
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action=action,
        entity_type="campaign",
        entity_id=campaign.id,
        details=details,
    )
