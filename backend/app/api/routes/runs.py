"""Runs and run items, read-only: `GET /api/runs`, `/api/runs/{id}`, `/api/run-items/{id}`.

A run's detail lists its items and how many are in each status; a run item's detail is
everything about one lead in one run: the lead card, its step timeline (`run_steps`,
written by `@tracked_node`), the score breakdown and the human's decision.

The score breakdown and extraction issues aren't columns: they're read from the item's
`score` and `extract` steps (`reviews_for()`, also used by the approval queue).
"""

from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, Paging, get_or_404, paginate
from app.api.errors import error_docs
from app.db.models import (
    Approval,
    Campaign,
    Lead,
    Run,
    RunItem,
    RunStatus,
    RunStep,
    StepStatus,
    User,
)
from app.schemas.common import Page, Review
from app.schemas.leads import LeadSummary
from app.schemas.runs import (
    ApprovalOut,
    RunDetail,
    RunItemDetail,
    RunItemRow,
    RunOut,
    StepOut,
)

router = APIRouter(tags=["runs"], responses=error_docs(401))


@router.get("/runs")
def list_runs(
    _user: CurrentUser,
    db: DbSession,
    paging: Paging,
    campaign_id: Annotated[int | None, Query(description="only this campaign's runs")] = None,
    status: Annotated[RunStatus | None, Query(description="only runs in this status")] = None,
) -> Page[RunOut]:
    """Runs, newest first."""
    stmt = (
        select(Run, Campaign.name)
        .join(Campaign, Campaign.id == Run.campaign_id)
        .order_by(Run.created_at.desc(), Run.id.desc())
    )
    if campaign_id is not None:
        stmt = stmt.where(Run.campaign_id == campaign_id)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    rows, total = paginate(db, stmt, paging)
    return Page(
        items=[run_out(run, name) for run, name in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/runs/{run_id}", responses=error_docs(404))
def get_run(run_id: int, _user: CurrentUser, db: DbSession) -> RunDetail:
    """The run, its items (in the order they were collected) and the count per status."""
    run = get_or_404(db, Run, run_id, "run")
    campaign = db.get(Campaign, run.campaign_id)
    rows = db.execute(run_items_query(run.id)).all()
    items = [item_row(item, name) for item, name in rows]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return RunDetail(**run_out(run, campaign.name).model_dump(), counts=counts, items=items)


@router.get("/run-items/{item_id}", responses=error_docs(404))
def get_run_item(item_id: int, _user: CurrentUser, db: DbSession) -> RunItemDetail:
    """One lead in one run: lead card, step timeline, score breakdown, decision."""
    item = get_or_404(db, RunItem, item_id, "run item")
    run = db.get(Run, item.run_id)
    steps = db.scalars(steps_query(item.id)).all()
    return RunItemDetail(
        id=item.id,
        run_id=item.run_id,
        campaign_id=run.campaign_id,
        thread_id=item.thread_id,
        status=item.status,
        route=item.route,
        score=item.score,
        flags=item.flags,
        extraction_ok=item.extraction_ok,
        notified_at=item.notified_at,
        error=item.error,
        created_at=item.created_at,
        updated_at=item.updated_at,
        lead=LeadSummary.model_validate(db.get(Lead, item.lead_id)),
        review=reviews_for(db, [item.id])[item.id],
        steps=[StepOut.model_validate(step) for step in steps],
        approval=approval_for(db, item.id),
    )


def run_items_query(run_id: int) -> Select[tuple[RunItem, str]]:
    """A run's items in the order they were collected, with the lead's name."""
    return (
        select(RunItem, Lead.name)
        .join(Lead, Lead.id == RunItem.lead_id)
        .where(RunItem.run_id == run_id)
        .order_by(RunItem.id)
    )


def steps_query(item_id: int) -> Select[tuple[RunStep]]:
    """A run item's step timeline, oldest first."""
    return (
        select(RunStep)
        .where(RunStep.run_item_id == item_id)
        .order_by(RunStep.started_at, RunStep.id)
    )


def run_out(run: Run, campaign_name: str) -> RunOut:
    return RunOut.model_validate(run).model_copy(update={"campaign_name": campaign_name})


def item_row(item: RunItem, lead_name: str) -> RunItemRow:
    return RunItemRow(
        id=item.id,
        lead_id=item.lead_id,
        lead_name=lead_name,
        status=item.status,
        route=item.route,
        score=item.score,
        flags=item.flags,
        extraction_ok=item.extraction_ok,
        error=item.error,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def reviews_for(db: Session, item_ids: Iterable[int]) -> dict[int, Review]:
    """Each item's score breakdown + explanation (its `score` step) and extraction issues
    (its `extract` step). An item that hasn't got that far gets an empty `Review`."""
    item_ids = list(item_ids)
    reviews = {item_id: Review() for item_id in item_ids}
    steps = db.scalars(
        select(RunStep)
        .where(
            RunStep.run_item_id.in_(item_ids),
            RunStep.node.in_(("score", "extract")),
            RunStep.status == StepStatus.COMPLETED,
        )
        .order_by(RunStep.id)  # a later row wins (there's only one per node, see tracking)
    )
    for step in steps:
        output = step.output or {}
        review = reviews[step.run_item_id]
        if step.node == "score":
            review.score_breakdown = output.get("score_breakdown") or []
            review.score_explanation = output.get("score_explanation")
        else:
            review.extraction_issues = output.get("extraction_issues") or []
    return reviews


def approval_for(db: Session, item_id: int) -> ApprovalOut | None:
    row = db.execute(
        select(Approval, User.email)
        .join(User, User.id == Approval.reviewer_id)
        .where(Approval.run_item_id == item_id)
    ).first()
    if row is None:
        return None
    approval, email = row
    return approval_out(approval, email)


def approval_out(approval: Approval, reviewer_email: str) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        run_item_id=approval.run_item_id,
        decision=approval.decision,
        reason=approval.reason,
        reviewer_id=approval.reviewer_id,
        reviewer_email=reviewer_email,
        created_at=approval.created_at,
    )
