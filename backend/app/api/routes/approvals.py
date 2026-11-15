"""The human in the loop: `GET /api/approvals/queue` and `POST /api/run-items/{id}/decision`.

The queue lists run items paused at the approval gate, newest first unless `sort` says
otherwise (`created_at`: oldest first; `-score`: best first, unscored last), each with the
lead card, the flags, the score breakdown and the rubric that scored it. A decision goes
through PLAN §6's approval transaction (`app.services.approvals.decide`: row lock, status
check, approval + audit row + `resume_item` job, one commit). If the item isn't waiting
any more (someone else decided first, or it isn't at the gate yet) the answer is 409; the
worker then resumes the thread with the decision.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, select

from app.api.deps import AppSettings, DbSession, Paging, ReviewerUser, paginate
from app.api.errors import error_docs
from app.api.routes.auth import is_demo
from app.api.routes.runs import approval_out, reviews_for
from app.db.models import Campaign, Lead, Run, RunItem, RunItemStatus
from app.schemas.common import Page
from app.schemas.leads import LeadSummary
from app.schemas.runs import ApprovalOut, DecisionIn, QueueItem
from app.services.approvals import LeadOptedOut, NotAwaitingDecision, decide

router = APIRouter(tags=["approvals"], responses=error_docs(401, 403))

QueueSort = Literal["-created_at", "created_at", "-score"]
# Each ends with the id, so items with the same time or score keep a stable order.
QUEUE_SORTS = {
    "-created_at": (RunItem.created_at.desc(), RunItem.id.desc()),
    "created_at": (RunItem.created_at.asc(), RunItem.id.asc()),
    "-score": (RunItem.score.desc().nulls_last(), RunItem.created_at.desc(), RunItem.id.desc()),
}


@router.get("/approvals/queue")
def approval_queue(
    _reviewer: ReviewerUser,
    db: DbSession,
    paging: Paging,
    campaign_id: Annotated[int | None, Query(description="only this campaign's items")] = None,
    sort: QueueSort = "-created_at",
) -> Page[QueueItem]:
    """Run items waiting for a decision, newest first unless `sort` says otherwise."""
    rows, total = paginate(db, queue_query(campaign_id, sort), paging)
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


def queue_query(
    campaign_id: int | None = None, sort: QueueSort = "-created_at"
) -> Select[tuple[RunItem, Lead, int, str]]:
    """The approval queue's query (also measured by `scripts/sql_explain.py`)."""
    stmt = (
        select(RunItem, Lead, Run.campaign_id, Campaign.name)
        .join(Lead, Lead.id == RunItem.lead_id)
        .join(Run, Run.id == RunItem.run_id)
        .join(Campaign, Campaign.id == Run.campaign_id)
        .where(RunItem.status == RunItemStatus.AWAITING_APPROVAL)
        .order_by(*QUEUE_SORTS[sort])
    )
    if campaign_id is not None:
        stmt = stmt.where(Run.campaign_id == campaign_id)
    return stmt


# The public demo's reviewer login is shared by every visitor, so a reason typed there
# would show to all the others (lead history, the run item's steps, the audit log): it's
# saved as this instead, before it reaches any of them.
DEMO_REASON = "(a demo visitor's reason: not kept)"


@router.post("/run-items/{item_id}/decision", responses=error_docs(404, 409, 422))
def decide_item(
    item_id: int, body: DecisionIn, reviewer: ReviewerUser, db: DbSession, settings: AppSettings
) -> ApprovalOut:
    """Approve or reject a waiting item (a rejection needs a reason; on the public demo's
    login a reason is saved as a placeholder, not as typed).

    409 if the item isn't waiting, or if its lead opted out and this is an approval.
    """
    reason = body.reason
    if reason and reason.strip() and is_demo(reviewer.email, settings):
        reason = DEMO_REASON
    # No lookup first: `decide()` loads the item itself, with the row lock.
    try:
        approval = decide(db, item_id, reviewer, body.decision, reason, datetime.now(UTC))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run item {item_id} not found") from exc
    except (NotAwaitingDecision, LeadOptedOut) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:  # e.g. a rejection without a reason
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return approval_out(approval, reviewer.email)
