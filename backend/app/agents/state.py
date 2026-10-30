"""What flows through the lead graph: the state, the extraction result, and the context.

`LeadState` is the graph's memory for one lead (one LangGraph thread). Each node
reads what it needs and returns only the keys it changes; the checkpointer saves the
state after every node, so a crash resumes from the last finished node.

`AgentContext` is what the nodes get besides the state: the DB session, the LLM,
the crawler, the CRM, the notifier and the tracer. It is *not* saved in checkpoints (it's live
objects, not data), so the caller passes it again on every run and resume:
`graph.invoke(..., context=AgentContext(...))`.

Changes from PLAN §5's state (logged in Decisions): `thread_id` (the idempotency key
for the CRM), `rubric_id` (runs pin their rubric) and `category_matches_niche` (set by
`check`, read by the rubric).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.llm import LLM
from app.enrichment.crawler import Crawler
from app.observability.tracing import Tracer
from app.services.crm import CRMClient
from app.services.notify import Notifier


class LeadState(TypedDict, total=False):
    # Given when the thread starts (by app.agents.runner, called by the worker).
    thread_id: str
    run_item_id: int | None
    lead_id: int
    campaign_id: int
    rubric_id: int
    correlation_id: str | None

    # enrich
    raw: dict[str, Any]  # the lead as collected (see cleaning.lead.lead_snapshot)
    enrichment: dict[str, Any]  # website text, emails, phones, socials, signals
    # extract
    cleaned: dict[str, Any]  # after deterministic cleaning
    extracted: dict[str, Any]  # ExtractedLead.model_dump()
    extraction_ok: bool
    extraction_issues: list[str]
    # check
    flags: list[str]  # "possible_duplicate:<lead_id>", "category_mismatch", "closed", ...
    category_matches_niche: bool
    # score
    score: int  # 0–100
    score_breakdown: list[dict[str, Any]]  # [{"rule", "field", "points", "hit", "required"}]
    score_explanation: str
    # approval_gate (from the human's answer)
    decision: str  # "approved" | "rejected"
    reason: str | None
    reviewer_id: int | None
    # route
    route: str  # "notify" | "skip" | "escalate"
    # act
    crm_record_id: str | None
    notified: bool


class ExtractedLead(BaseModel):
    """The clean, structured lead after `extract`. Every value is traceable to the data."""

    name: str
    phone: str | None  # E.164
    email: str | None  # best public business email
    website: str | None
    address: str | None
    area: str | None
    city: str | None
    country: str | None
    category: str | None  # from app.agents.categories.CATEGORIES
    services: list[str] = Field(default_factory=list)  # short phrases found on the website
    established_year: int | None
    verified: bool | None
    rating: float | None  # 0–5
    review_count: int | None


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentContext:
    """Live helpers for the nodes (never checkpointed)."""

    session: Session
    llm: LLM
    crawler: Crawler
    crm: CRMClient
    notifier: Notifier
    enrich_cache_days: int = 14
    # Langfuse spans per node (the default does nothing: tracing off).
    tracer: Tracer = field(default_factory=Tracer)
    # Tests pass a fixed clock.
    now: Callable[[], datetime] = field(default=utc_now)
