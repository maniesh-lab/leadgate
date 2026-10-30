"""Bodies for `/api/leads`, plus the short lead card the run and approval answers embed."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import Decision, LeadStatus, Route, RunItemStatus
from app.schemas.common import FromRow, Review


class LeadSummary(FromRow):
    """The fields a reviewer looks at first."""

    id: int
    name: str
    phone: str | None
    email: str | None
    website: str | None
    address: str | None
    area: str | None
    city: str | None
    category: str | None
    rating: float | None
    review_count: int | None
    score: int | None
    status: LeadStatus
    opt_out: bool


class LeadRow(LeadSummary):
    """A row in the leads table."""

    source: str
    country: str | None
    website_domain: str | None
    enriched_at: datetime | None
    first_seen_at: datetime
    updated_at: datetime


class LeadRunItem(BaseModel):
    """The lead in one run: what the agents and the reviewer decided."""

    run_item_id: int
    run_id: int
    campaign_id: int
    campaign_name: str
    status: RunItemStatus
    route: Route | None
    score: int | None
    flags: list[str]
    decision: Decision | None
    reason: str | None
    reviewer_email: str | None
    created_at: datetime


class LeadDetail(LeadRow):
    source_ref: str
    services: list[str]
    established_year: int | None
    verified: bool | None
    business_status: str | None
    lat: float | None
    lng: float | None
    crm_record_id: str | None
    # The website enrichment without the page text: emails, phones, socials, signals, pages.
    enrichment: dict[str, Any] | None
    # The score breakdown + explanation from the lead's latest run.
    review: Review
    # Every run the lead was in, newest first.
    history: list[LeadRunItem]
