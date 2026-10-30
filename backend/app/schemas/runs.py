"""Bodies for runs and run items (`/api/runs`, `/api/run-items`, `/api/campaigns/{id}/runs`)
and the approval queue (`/api/approvals/queue`, `/api/run-items/{id}/decision`)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.models import Decision, Route, RunItemStatus, RunStatus, RunTrigger, StepStatus
from app.schemas.common import FromRow
from app.schemas.leads import LeadSummary


class RunOut(FromRow):
    id: int
    campaign_id: int
    campaign_name: str | None = None
    trigger: RunTrigger
    triggered_by: int | None
    rubric_id: int
    status: RunStatus
    correlation_id: str | None
    # {"collected": 20, "new": 18, ..., "items": {"done": 15, "awaiting_approval": 5}}
    stats: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunItemRow(BaseModel):
    """One lead in a run, as a row in the run's item list."""

    id: int
    lead_id: int
    lead_name: str
    status: RunItemStatus
    route: Route | None
    score: int | None
    flags: list[str]
    extraction_ok: bool | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class RunDetail(RunOut):
    # How many items are in each status, e.g. {"done": 15, "awaiting_approval": 5}.
    counts: dict[str, int]
    items: list[RunItemRow]


class StepOut(FromRow):
    """One node run on the item (from `run_steps`). Long values in `output` are cut."""

    id: int
    node: str
    status: StepStatus
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class ApprovalOut(BaseModel):
    id: int
    run_item_id: int
    decision: Decision
    reason: str | None
    reviewer_id: int
    reviewer_email: str
    created_at: datetime


class Review(BaseModel):
    """Why the lead scored what it did, and what extraction couldn't prove (from its steps)."""

    score_breakdown: list[dict[str, Any]] = []
    score_explanation: str | None = None
    extraction_issues: list[str] = []


class RunItemDetail(BaseModel):
    id: int
    run_id: int
    campaign_id: int
    thread_id: str
    status: RunItemStatus
    route: Route | None
    score: int | None
    flags: list[str]
    extraction_ok: bool | None
    notified_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    lead: LeadSummary
    review: Review
    steps: list[StepOut]
    approval: ApprovalOut | None


class QueueItem(BaseModel):
    """A run item waiting for a human: the lead card plus what the agents found."""

    run_item_id: int
    run_id: int
    campaign_id: int
    campaign_name: str
    score: int | None
    flags: list[str]
    extraction_ok: bool | None
    created_at: datetime
    # When it reached the approval gate (its last update).
    waiting_since: datetime
    lead: LeadSummary
    review: Review


class DecisionIn(BaseModel):
    decision: Decision
    # Required for a rejection.
    reason: str | None = Field(default=None, max_length=1000)
