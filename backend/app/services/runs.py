"""Runs: start one, fill it with the leads its sources found, keep its status up to date.

A run's life (statuses from PLAN §6):

    queued      `create_run()`: the run row + its `start_run` job, in one transaction
    collecting  the worker's `start_run` job runs the campaign's sources (`collect_run()`)
    running     one run_item + one `process_item` job per lead are created
    waiting_approval   every item is paused at the approval gate (or done)
    completed   every item is done or failed (`refresh_run_status()`)
    failed      `start_run` gave up (e.g. every source failed three times)

Each lead gets its own LangGraph thread, `run-<run id>-lead-<lead id>`. Called by the
CLI (`start-run`), the worker's job handlers and the API (`POST /api/campaigns/{id}/runs`,
and the n8n webhook).

A run started by n8n can bring its own leads (they ride in the `start_run` job's payload
and replace the campaign's sources for that run) and a `callback_url`: when the run
completes or fails, a `send_callback` job posts its summary there (`app.services.n8n`).
"""

import uuid
from datetime import datetime
from typing import Any

import httpx2
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    ActorType,
    Campaign,
    JobType,
    Lead,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunTrigger,
    User,
)
from app.services.audit import record
from app.services.ingestion import collect_campaign, collect_inline
from app.sources.base import SourceError
from app.worker.jobs import enqueue

log = structlog.get_logger(__name__)

BUSY = (RunItemStatus.PENDING, RunItemStatus.RUNNING, RunItemStatus.RESUMING)


def create_run(
    session: Session,
    campaign: Campaign,
    now: datetime,
    *,
    trigger: RunTrigger = RunTrigger.MANUAL,
    triggered_by: User | None = None,
    callback_url: str | None = None,
    correlation_id: str | None = None,
    leads: list[dict[str, Any]] | None = None,
) -> Run:
    """Add a queued run and its `start_run` job, and commit both together.

    `leads` (from the n8n webhook) are this run's leads instead of the campaign's sources.
    """
    run = Run(
        campaign_id=campaign.id,
        trigger=trigger,
        triggered_by=triggered_by.id if triggered_by else None,
        # Pinned now, so editing the campaign later doesn't change this run.
        rubric_id=campaign.rubric_id,
        status=RunStatus.QUEUED,
        callback_url=callback_url,
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    session.add(run)
    session.flush()  # gives the run its id
    payload = job_payload(run)
    if leads is not None:
        payload["leads"] = leads
    enqueue(session, JobType.START_RUN, payload, now)
    record(
        session,
        actor_type=ActorType.USER if triggered_by else ActorType.SYSTEM,
        actor=triggered_by.email if triggered_by else trigger.value,
        action="run.created",
        entity_type="run",
        entity_id=run.id,
        run_id=run.id,
        details={"campaign_id": campaign.id, "trigger": trigger.value},
        correlation_id=run.correlation_id,
    )
    session.commit()
    log.info("run_created", run_id=run.id, campaign_id=campaign.id)
    return run


def collect_run(
    session: Session,
    run: Run,
    settings: Settings,
    client: httpx2.Client,
    now: datetime,
    leads: list[dict[str, Any]] | None = None,
) -> None:
    """The `start_run` job: collect the leads, then add one item + one job per lead.

    With `leads` (sent by n8n), those are saved instead of running the campaign's sources.

    The items and their jobs are committed together, so a retry after a crash either
    finds them all (and does nothing) or none (and collects again; the upsert makes
    that safe).
    """
    if has_items(session, run):
        log.info("run_already_collected", run_id=run.id)
        return

    run.status = RunStatus.COLLECTING
    run.started_at = run.started_at or now
    audit_run(session, run, "run.started")
    session.commit()

    if leads is not None:
        outcomes = collect_inline(session, leads)
    else:
        campaign = session.get(Campaign, run.campaign_id)
        outcomes = collect_campaign(session, campaign, settings, client)
    failed = {outcome.source: outcome.error for outcome in outcomes if outcome.error}
    if outcomes and len(failed) == len(outcomes):
        # Raise, so the job is retried (sources like Overpass are often busy for a while).
        raise SourceError(f"every source failed: {failed}")

    keys = [key for outcome in outcomes if outcome.result for key in outcome.result.saved_keys]
    lead_ids = lead_ids_for(session, keys)
    for lead_id in lead_ids:
        item = RunItem(
            run_id=run.id,
            lead_id=lead_id,
            thread_id=f"run-{run.id}-lead-{lead_id}",
            status=RunItemStatus.PENDING,
        )
        session.add(item)
        session.flush()  # gives the item its id
        enqueue(session, JobType.PROCESS_ITEM, job_payload(run, run_item_id=item.id), now)
        audit_run(session, run, "lead.collected", entity_type="lead", entity_id=lead_id)

    run.stats = collect_stats(outcomes, failed, len(lead_ids))
    run.status = RunStatus.RUNNING
    refresh_run_status(session, run, now)  # an empty run is completed straight away
    session.commit()
    log.info("run_collected", run_id=run.id, items=len(lead_ids), failed_sources=failed)


def refresh_run_status(session: Session, run: Run, now: datetime) -> None:
    """Set the run's status from its items' statuses. The caller commits.

    Locks the run row first (`FOR UPDATE`), so two workers finishing the last two items
    at the same moment can't both miss that the run is complete.
    """
    # Flush first: refresh() reloads the row and would throw away unsaved changes.
    session.flush()
    session.refresh(run, with_for_update=True)
    if run.status not in (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL):
        return  # still collecting, or already finished
    rows = session.execute(
        select(RunItem.status, func.count())
        .where(RunItem.run_id == run.id)
        .group_by(RunItem.status)
    )
    counts = {status.value: count for status, count in rows}

    if any(counts.get(status.value) for status in BUSY):
        run.status = RunStatus.RUNNING
    elif counts.get(RunItemStatus.AWAITING_APPROVAL.value):
        run.status = RunStatus.WAITING_APPROVAL
    else:
        run.status = RunStatus.COMPLETED
        run.finished_at = now
        audit_run(session, run, "run.completed", details={"items": counts})
        queue_callback(session, run, now)
        log.info("run_completed", run_id=run.id, items=counts)
    # A new dict, so SQLAlchemy sees the JSON column changed.
    run.stats = {**(run.stats or {}), "items": counts}


def fail_run(session: Session, run: Run, error: str, now: datetime) -> None:
    """`start_run` gave up: the run is failed. The caller commits."""
    run.status = RunStatus.FAILED
    run.finished_at = now
    run.stats = {**(run.stats or {}), "error": error}
    audit_run(session, run, "run.failed", details={"error": error})
    queue_callback(session, run, now)


def queue_callback(session: Session, run: Run, now: datetime) -> None:
    """A finished run with a `callback_url` (n8n) gets a `send_callback` job."""
    if run.callback_url:
        enqueue(session, JobType.SEND_CALLBACK, job_payload(run), now)


def job_payload(run: Run, **ids: int) -> dict[str, Any]:
    """Every job carries the run's correlation id, so the worker's logs can be followed."""
    return {"run_id": run.id, **ids, "correlation_id": run.correlation_id}


def has_items(session: Session, run: Run) -> bool:
    return session.scalar(select(RunItem.id).where(RunItem.run_id == run.id).limit(1)) is not None


def lead_ids_for(session: Session, keys: list[str]) -> list[int]:
    """Lead ids for these source keys, in a stable order, without opted-out leads."""
    if not keys:
        return []
    stmt = (
        select(Lead.id)
        .where(Lead.source_key.in_(set(keys)), Lead.opt_out.is_(False))
        .order_by(Lead.id)
    )
    return list(session.scalars(stmt))


def collect_stats(outcomes: list, failed: dict[str, str], items: int) -> dict[str, Any]:
    results = [outcome.result for outcome in outcomes if outcome.result]
    return {
        "collected": sum(result.seen for result in results),
        "new": sum(result.new for result in results),
        "updated": sum(result.updated for result in results),
        "duplicates": sum(result.duplicates for result in results),
        "opted_out": sum(result.opted_out for result in results),
        "bad_rows": sum(result.skipped for result in results),
        "run_items": items,
        "skipped_sources": {o.source: o.skipped for o in outcomes if o.skipped},
        "failed_sources": failed,
    }


def audit_run(
    session: Session,
    run: Run,
    action: str,
    *,
    entity_type: str = "run",
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    record(
        session,
        actor_type=ActorType.SYSTEM,
        actor="worker",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id if entity_id is not None else run.id,
        run_id=run.id,
        details=details,
        correlation_id=run.correlation_id,
    )
