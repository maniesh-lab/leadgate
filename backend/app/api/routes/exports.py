"""Exports (`/api/exports/*`): download data as a CSV file. Admins only.

`GET /api/exports/approved-leads?run_id=` (or `?campaign_id=`) answers a run's or a
campaign's approved leads, without the opted-out ones (`app.services.exports`). Each
download writes a `leads.exported` audit row: contact data leaving the app is worth a
trace, even though nothing in the database changes.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import AdminUser, DbSession, get_or_404
from app.api.errors import error_docs
from app.db.models import ActorType, Campaign, Run
from app.services.audit import record
from app.services.exports import approved_leads_csv

router = APIRouter(prefix="/exports", tags=["exports"], responses=error_docs(401, 403))


@router.get(
    "/approved-leads",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {"schema": {"type": "string"}}}},
        **error_docs(404),
    },
)
def export_approved_leads(
    admin: AdminUser,
    db: DbSession,
    run_id: Annotated[int | None, Query(description="one run's approved leads")] = None,
    campaign_id: Annotated[
        int | None, Query(description="every run of a campaign (one row per lead)")
    ] = None,
) -> Response:
    """A CSV of approved leads (escalated ones too, see `route`); opted-out leads left out."""
    if (run_id is None) == (campaign_id is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "give either run_id or campaign_id"
        )
    if run_id is not None:
        get_or_404(db, Run, run_id, "run")
        filename = f"leadgate-run-{run_id}-approved.csv"
    else:
        get_or_404(db, Campaign, campaign_id, "campaign")
        filename = f"leadgate-campaign-{campaign_id}-approved.csv"

    text, count = approved_leads_csv(db, run_id=run_id, campaign_id=campaign_id)
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="leads.exported",
        entity_type="run" if run_id is not None else "campaign",
        entity_id=run_id if run_id is not None else campaign_id,
        run_id=run_id,
        details={"rows": count, "format": "csv"},
    )
    db.commit()
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
