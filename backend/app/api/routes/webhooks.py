"""The n8n webhook (`POST /api/webhooks/n8n/runs`): Switchyard's n8n starts a run.

No login: n8n proves who it is with the shared `X-Webhook-Secret` header
(N8N_WEBHOOK_SECRET). Without that setting the webhook is off (503).

The body names the campaign by `campaign_id`, or by `niche` + `location`: the newest
open campaign with the same niche and location (any case) is used, else a new one is
made (rubric `general-b2b`, source `osm`; with no source when the call brings its own
`leads`, since those are its list). Like `POST /api/campaigns/{id}/runs`, the route only
writes the run row and its `start_run` job; the worker does the rest, and posts the
results to `callback_url` at the end (`app.services.n8n`).
"""

import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CorrelationId, DbSession, get_or_404
from app.api.errors import error_docs
from app.api.routes.campaigns import check_campaign
from app.db.models import ActorType, Campaign, RunTrigger, ScoringRubric
from app.schemas.webhooks import N8nRunAccepted, N8nRunRequest
from app.services.audit import record
from app.services.n8n import SECRET_HEADER
from app.services.runs import create_run

# The rubric a campaign made by the webhook gets (seeded by `python -m app.cli seed`).
DEFAULT_RUBRIC = "general-b2b"

secret_header = APIKeyHeader(name=SECRET_HEADER, auto_error=False, scheme_name="n8nSecret")


def check_secret(
    settings: AppSettings, secret: Annotated[str | None, Security(secret_header)]
) -> None:
    """401 unless the header matches N8N_WEBHOOK_SECRET; 503 when the setting is empty."""
    if not settings.n8n_webhook_secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "the n8n webhook is off (N8N_WEBHOOK_SECRET)"
        )
    # compare_digest: takes the same time however much of the secret matches.
    if not secret or not hmac.compare_digest(secret, settings.n8n_webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"wrong or missing {SECRET_HEADER}")


router = APIRouter(prefix="/webhooks", tags=["webhooks"], dependencies=[Depends(check_secret)])


@router.post(
    "/n8n/runs",
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_docs(401, 404, 409, 503),
)
def n8n_run(body: N8nRunRequest, db: DbSession, correlation_id: CorrelationId) -> N8nRunAccepted:
    """Queue a run for n8n; its summary is posted to `callback_url` when it's finished."""
    campaign = campaign_for(db, body)
    run = create_run(
        db,
        campaign,
        datetime.now(UTC),
        trigger=RunTrigger.N8N,
        callback_url=str(body.callback_url) if body.callback_url else None,
        correlation_id=correlation_id,
        leads=body.leads,
    )
    return N8nRunAccepted(run_id=run.id, campaign_id=campaign.id, status=run.status)


def campaign_for(db: Session, body: N8nRunRequest) -> Campaign:
    if body.campaign_id is not None:
        campaign = get_or_404(db, Campaign, body.campaign_id, "campaign")
        if campaign.is_archived:
            raise HTTPException(status.HTTP_409_CONFLICT, f"campaign {campaign.id} is archived")
        return campaign
    return find_campaign(db, body.niche, body.location) or new_campaign(db, body)


def find_campaign(db: Session, niche: str, location: str) -> Campaign | None:
    stmt = (
        select(Campaign)
        .where(func.lower(Campaign.niche) == niche.lower())
        .where(func.lower(Campaign.location) == location.lower())
        .where(Campaign.is_archived.is_(False))
        .order_by(Campaign.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def new_campaign(db: Session, body: N8nRunRequest) -> Campaign:
    rubric_id = db.scalar(
        select(ScoringRubric.id)
        .where(ScoringRubric.name == DEFAULT_RUBRIC)
        .order_by(ScoringRubric.version.desc())
        .limit(1)
    )
    if rubric_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"no {DEFAULT_RUBRIC!r} rubric yet: run `python -m app.cli seed` first",
        )
    campaign = Campaign(
        name=f"{body.niche} in {body.location} (n8n)",
        niche=body.niche,
        location=body.location,
        sources=[] if body.leads else ["osm"],
        source_options={},
        rubric_id=rubric_id,
    )
    check_campaign(db, campaign)  # 422 if OSM doesn't know the niche
    db.add(campaign)
    db.flush()
    record(
        db,
        actor_type=ActorType.SYSTEM,
        actor="n8n",
        action="campaign.created",
        entity_type="campaign",
        entity_id=campaign.id,
        details={"niche": campaign.niche, "location": campaign.location},
    )
    return campaign  # committed with the run by create_run()
