"""The human in the loop: `GET /api/approvals/queue` and `POST /api/run-items/{id}/decision`.

The queue lists run items paused at the approval gate, oldest first, each with the lead
card, the flags and the score breakdown. A decision goes through PLAN §6's approval
transaction (`app.services.approvals.decide`: row lock, status check, approval + audit
row + `resume_item` job, one commit). If the item isn't waiting any more (someone else
decided first, or it isn't at the gate yet) the answer is 409; the worker then resumes
the thread with the decision.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, Paging, ReviewerUser, paginate
from app.api.errors import error_docs
from app.api.routes.runs import approval_out, reviews_for
from app.db.models import Campaign, Lead, Run, RunItem, RunItemStatus
from app.schemas.common import Page
from app.schemas.leads import LeadSummary
from app.schemas.runs import ApprovalOut, DecisionIn, QueueItem
from app.services.approvals import NotAwaitingDecision, decide

router = APIRouter(tags=["approvals"], responses=error_docs(401, 403))


@router.get("/approvals/queue")
def approval_queue(
    _reviewer: ReviewerUser,
    db: DbSession,
    paging: Paging,
    campaign_id: Annotated[int | None, Query(description="only this campaign's items")] = None,
) -> Page[QueueItem]:
    """Run items waiting for a decision, oldest first."""
    stmt = (
        select(RunItem, Lead, Run.campaign_id, Campaign.name)
        .join(Lead, Lead.id == RunItem.lead_id)
        .join(Run, Run.id == RunItem.run_id)
        .join(Campaign, Campaign.id == Run.campaign_id)
        .where(RunItem.status == RunItemStatus.AWAITING_APPROVAL)
        .order_by(RunItem.created_at, RunItem.id)
    )
    if campaign_id is not None:
        stmt = stmt.where(Run.campaign_id == campaign_id)
    rows, total = paginate(db, stmt, paging)
    reviews = reviews_for(db, [item.id for item, *_ in rows])
    items = [
        QueueItem(
            run_item_id=item.id,
            run_id=item.run_id,
            campaign_id=item_campaign_id,
            campaign_name=campaign_name,
            score=item.score,
            flags=item.flags,
            extraction_ok=item.extraction_ok,
            created_at=item.created_at,
            waiting_since=item.updated_at,
            lead=LeadSummary.model_validate(lead),
            review=reviews[item.id],
        )
        for item, lead, item_campaign_id, campaign_name in rows
    ]
    return Page(items=items, total=total, page=paging.page, page_size=paging.page_size)


@router.post("/run-items/{item_id}/decision", responses=error_docs(404, 409, 422))
def decide_item(
    item_id: int, body: DecisionIn, reviewer: ReviewerUser, db: DbSession
) -> ApprovalOut:
    """Approve or reject a waiting item (a rejection needs a reason). 409 if it isn't waiting."""
    # No lookup first: `decide()` loads the item itself, with the row lock.
    try:
        approval = decide(db, item_id, reviewer, body.decision, body.reason, datetime.now(UTC))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run item {item_id} not found") from exc
    except NotAwaitingDecision as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:  # e.g. a rejection without a reason
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return approval_out(approval, reviewer.email)
