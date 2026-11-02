"""Retry a failed lead: put its run item back on the job queue, in one transaction.

An item is `failed` when its job gave up after `max_attempts` (e.g. the site or the LLM
kept timing out, or the CRM refused the write). `retry_item()`:

1. locks the run item row (`SELECT ... FOR UPDATE`), so two clicks can't both retry it
2. checks it is still `failed` (else `NotFailed`, a 409 in the API)
3. picks the job that's left: `resume_item` if a reviewer already decided (the item failed
   in route / act), else `process_item`; the item goes back to `resuming` / `pending`
4. reopens the run if it was `completed`, adds a `run_item.retried` audit row and the job
5. commits

Nothing runs twice: the worker's handlers start from the thread's last checkpoint, so
nodes that completed before the failure are skipped, and `act`'s CRM write and message
are keyed to the item (`app.worker.handlers`). When the reopened run completes again, it
writes another `run.completed` row, and an n8n run gets an updated results callback.

Called by the API (`POST /api/run-items/{id}/retry`).
"""

from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ActorType,
    Approval,
    Decision,
    JobType,
    Lead,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    User,
)
from app.services.approvals import LeadOptedOut
from app.services.audit import record
from app.services.runs import job_payload, refresh_run_status
from app.worker.jobs import enqueue

log = structlog.get_logger(__name__)


class NotFailed(Exception):
    """Only a failed item can be retried (this one is waiting, running or done)."""


def retry_item(session: Session, run_item_id: int, admin: User, now: datetime) -> RunItem:
    """Queue the failed item again. Raises LookupError, NotFailed or LeadOptedOut."""
    item = session.get(RunItem, run_item_id, with_for_update=True)
    if item is None:
        raise LookupError(f"run item {run_item_id} not found")
    if item.status != RunItemStatus.FAILED:
        session.rollback()  # releases the lock
        raise NotFailed(f"run item {run_item_id} is {item.status.value}, not failed")

    approval = session.scalar(select(Approval).where(Approval.run_item_id == item.id))
    approved = approval is not None and approval.decision == Decision.APPROVED
    if approved and session.get(Lead, item.lead_id).opt_out:
        # Its checkpoint still holds the old contact data; `act` would send it to the CRM.
        session.rollback()
        raise LeadOptedOut(f"lead {item.lead_id} opted out; its approved item can't be retried")

    run = session.get(Run, item.run_id)
    job_type = JobType.RESUME_ITEM if approval is not None else JobType.PROCESS_ITEM
    previous_error = item.error
    item.status = RunItemStatus.RESUMING if approval is not None else RunItemStatus.PENDING
    item.error = None
    if run.status == RunStatus.COMPLETED:  # failed items still let a run complete
        run.status = RunStatus.RUNNING
        run.finished_at = None

    record(
        session,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="run_item.retried",
        entity_type="run_item",
        entity_id=item.id,
        run_id=item.run_id,
        run_item_id=item.id,
        details={"lead_id": item.lead_id, "job": job_type.value, "previous_error": previous_error},
        correlation_id=run.correlation_id,
    )
    enqueue(session, job_type, job_payload(run, run_item_id=item.id), now)
    refresh_run_status(session, run, now)
    session.commit()
    log.info("item_retried", run_item_id=item.id, job=job_type.value, admin=admin.email)
    return item
