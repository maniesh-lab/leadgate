"""A reviewer's decision on one run item, saved in one transaction.

`decide()` does PLAN §6's approval transaction:

1. lock the run item row (`SELECT ... FOR UPDATE`), so two reviewers can't both win
2. check it is still `awaiting_approval` (else `NotAwaitingDecision`; the API turns that
   into 409 in Phase 7)
3. add the `approvals` row, set the item to `resuming`, add the audit row and a
   `resume_item` job
4. commit

The unique `approvals.run_item_id` is the second guard: if two decisions ever got past
the lock, the database refuses the second one.

The decision itself reaches the graph later: the worker's `resume_item` job reads the
approval row and resumes the item's thread with it. Called by the CLI (`decide`) and,
from Phase 7, the API.
"""

from datetime import datetime

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.nodes.approval import check_decision
from app.db.models import (
    ActorType,
    Approval,
    Decision,
    JobType,
    Run,
    RunItem,
    RunItemStatus,
    User,
)
from app.services.audit import record
from app.services.runs import job_payload, refresh_run_status
from app.worker.jobs import enqueue

log = structlog.get_logger(__name__)


class NotAwaitingDecision(Exception):
    """The item isn't waiting for a decision (already decided, or not at the gate yet)."""


def decide(
    session: Session,
    run_item_id: int,
    reviewer: User,
    decision: str,
    reason: str | None,
    now: datetime,
) -> Approval:
    """Save the decision and queue the resume. ValueError for a bad decision/reason."""
    decision, reason = check_decision(decision, reason)
    item = session.get(RunItem, run_item_id, with_for_update=True)
    if item is None:
        raise LookupError(f"run item {run_item_id} not found")
    if item.status != RunItemStatus.AWAITING_APPROVAL:
        session.rollback()  # releases the lock
        raise NotAwaitingDecision(f"run item {run_item_id} is {item.status.value}")

    run = session.get(Run, item.run_id)
    approval = Approval(
        run_item_id=item.id, reviewer_id=reviewer.id, decision=Decision(decision), reason=reason
    )
    session.add(approval)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise NotAwaitingDecision(f"run item {run_item_id} already has a decision") from exc
    item.status = RunItemStatus.RESUMING
    record(
        session,
        actor_type=ActorType.USER,
        actor=reviewer.email,
        action=f"approval.{decision}",
        entity_type="run_item",
        entity_id=item.id,
        run_id=item.run_id,
        run_item_id=item.id,
        details={"lead_id": item.lead_id, "reason": reason},
        correlation_id=run.correlation_id,
    )
    enqueue(session, JobType.RESUME_ITEM, job_payload(run, run_item_id=item.id), now)
    refresh_run_status(session, run, now)  # back to "running" while the item resumes
    session.commit()
    log.info("decision_saved", run_item_id=item.id, decision=decision, reviewer=reviewer.email)
    return approval
