"""What the worker does for each job type, and what happens when a job gives up.

- `start_run`: run the campaign's sources and add the run's items (`collect_run()`)
- `process_item`: run the item's thread up to the approval gate
- `resume_item`: give the thread the reviewer's decision (from the `approvals` row) and
  run it to the end

Every handler is safe to run twice: it first looks at where the item's thread is
(`thread_status()`) and only does what is left. That's what lets a killed worker's job
simply run again. Run item statuses are set here, from the graph's result, never by
the nodes (PLAN §5).

`give_up()` runs when a job has failed `max_attempts` times: the item (or the run, for
`start_run`) is marked failed with the error.

Called by `app.worker.main.Worker`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx2
import structlog
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.llm import LLM
from app.agents.runner import (
    NEW,
    PAUSED,
    UNFINISHED,
    continue_lead,
    current_state,
    resume_lead,
    start_lead,
    thread_status,
)
from app.agents.state import AgentContext, utc_now
from app.config import Settings
from app.db.models import Approval, Job, JobType, Lead, Route, Run, RunItem, RunItemStatus
from app.enrichment.crawler import Crawler
from app.services.crm import CRMClient
from app.services.notify import Notifier
from app.services.runs import collect_run, fail_run, refresh_run_status

log = structlog.get_logger(__name__)


@dataclass
class Deps:
    """Everything the handlers need. Built once per worker (`app.worker.main`)."""

    settings: Settings
    session_factory: sessionmaker[Session]
    graph: CompiledStateGraph  # compiled with the Postgres checkpointer
    llm: LLM
    crawler: Crawler
    crm: CRMClient
    notifier: Notifier
    sources_client: httpx2.Client  # for OSM / Google Places
    now: Callable[[], datetime] = field(default=utc_now)

    def agent_context(self, session: Session) -> AgentContext:
        return AgentContext(
            session=session,
            llm=self.llm,
            crawler=self.crawler,
            crm=self.crm,
            notifier=self.notifier,
            enrich_cache_days=self.settings.enrich_cache_days,
            now=self.now,
        )


def start_run(deps: Deps, session: Session, payload: dict[str, Any]) -> None:
    run = get(session, Run, payload["run_id"])
    collect_run(session, run, deps.settings, deps.sources_client, deps.now())


def process_item(deps: Deps, session: Session, payload: dict[str, Any]) -> None:
    item = get(session, RunItem, payload["run_item_id"])
    if item.status not in (RunItemStatus.PENDING, RunItemStatus.RUNNING):
        log.info("item_already_processed", run_item_id=item.id, status=item.status)
        return
    lead = get(session, Lead, item.lead_id)
    if lead.opt_out:
        item.error = "skipped: the lead opted out before it was processed"
        finish_item(deps, session, item, RunItemStatus.DONE)
        return

    item.status = RunItemStatus.RUNNING
    run = get(session, Run, item.run_id)
    refresh_run_status(session, run, deps.now())
    session.commit()

    context = deps.agent_context(session)
    status = thread_status(deps.graph, item.thread_id)
    if status == NEW:
        start_lead(
            deps.graph,
            context,
            thread_id=item.thread_id,
            lead_id=item.lead_id,
            campaign_id=run.campaign_id,
            rubric_id=run.rubric_id,
            run_item_id=item.id,
            correlation_id=run.correlation_id,
        )
    elif status == UNFINISHED:
        continue_lead(deps.graph, context, item.thread_id)
    save_item(deps, session, item)


def resume_item(deps: Deps, session: Session, payload: dict[str, Any]) -> None:
    item = get(session, RunItem, payload["run_item_id"])
    if item.status != RunItemStatus.RESUMING:
        log.info("item_not_resuming", run_item_id=item.id, status=item.status)
        return
    approval = session.scalars(select(Approval).where(Approval.run_item_id == item.id)).one()

    context = deps.agent_context(session)
    status = thread_status(deps.graph, item.thread_id)
    if status == PAUSED:
        resume_lead(
            deps.graph,
            context,
            item.thread_id,
            decision=approval.decision.value,
            reason=approval.reason,
            reviewer_id=approval.reviewer_id,
        )
    elif status == UNFINISHED:  # died during route / act last time
        continue_lead(deps.graph, context, item.thread_id)
    save_item(deps, session, item)


HANDLERS: dict[JobType, Callable[[Deps, Session, dict[str, Any]], None]] = {
    JobType.START_RUN: start_run,
    JobType.PROCESS_ITEM: process_item,
    JobType.RESUME_ITEM: resume_item,
    # send_callback (the n8n results callback) comes in Phase 8.
}


def save_item(deps: Deps, session: Session, item: RunItem) -> None:
    """Copy the thread's result onto the item and set its status from where the thread is."""
    state = current_state(deps.graph, item.thread_id)
    item.score = state.get("score")
    item.flags = state.get("flags", [])
    item.extraction_ok = state.get("extraction_ok")
    item.route = Route(state["route"]) if state.get("route") else None
    item.error = None
    status = thread_status(deps.graph, item.thread_id)
    if status == PAUSED:
        finish_item(deps, session, item, RunItemStatus.AWAITING_APPROVAL)
    elif status == UNFINISHED:
        raise RuntimeError(f"thread {item.thread_id} stopped before the gate or the end")
    else:
        finish_item(deps, session, item, RunItemStatus.DONE)


def finish_item(deps: Deps, session: Session, item: RunItem, status: RunItemStatus) -> None:
    item.status = status
    refresh_run_status(session, get(session, Run, item.run_id), deps.now())
    session.commit()
    log.info("item_saved", run_item_id=item.id, status=status, score=item.score)


def give_up(deps: Deps, session: Session, job: Job) -> None:
    """The job failed for good: mark its item (or run) failed with the last error."""
    error = job.last_error or "unknown error"
    if job.type == JobType.START_RUN:
        fail_run(session, get(session, Run, job.payload["run_id"]), error, deps.now())
        session.commit()
    elif "run_item_id" in job.payload:
        item = get(session, RunItem, job.payload["run_item_id"])
        item.error = error
        finish_item(deps, session, item, RunItemStatus.FAILED)


def get[T](session: Session, model: type[T], row_id: int) -> T:
    row = session.get(model, row_id)
    if row is None:
        raise LookupError(f"{model.__name__} {row_id} not found")
    return row
