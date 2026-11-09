"""Runs and run items: `GET /api/runs`, `/api/runs/{id}`, `/api/run-items/{id}`, an
admin's `POST /api/run-items/{id}/retry` for a failed lead, and `DELETE /api/runs/{id}`
(admins; `app.services.deletion`).

`GET /api/runs/{id}/events` keeps the run page live: a Server-Sent Events stream that
sends the run's detail (the same shape as `/api/runs/{id}`) whenever it changes. The
server checks a cheap fingerprint every POLL_SECONDS (WAITING_POLL_SECONDS while the run
waits for decisions) and sends only on a change, so many open pages cost little. The
stream ends when the run finishes, is deleted, or after STREAM_MAX_SECONDS (the browser
then opens a new one, which checks the login again). The browser reads it with `fetch`,
so the login token stays in the Authorization header, never in the URL.

A run's detail lists its items and how many are in each status; a run item's detail is
everything about one lead in one run: the lead card, its step timeline (`run_steps`,
written by `@tracked_node`), the score breakdown and the human's decision.

A retry only queues a job (`app.services.retry.retry_item`: row lock, status check,
audit row + job, one commit); the worker continues the lead from its last checkpoint.

The score breakdown and extraction issues aren't columns: they're read from the item's
`score` and `extract` steps (`reviews_for()`, also used by the approval queue).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, get_or_404, paginate
from app.api.errors import error_docs
from app.db.models import (
    ActorType,
    Approval,
    Campaign,
    Lead,
    Run,
    RunItem,
    RunStatus,
    RunStep,
    ScoringRubric,
    StepStatus,
    User,
)
from app.schemas.common import Page, Review, RubricRef
from app.schemas.leads import LeadSummary
from app.schemas.runs import (
    ApprovalOut,
    RunDetail,
    RunItemDetail,
    RunItemRow,
    RunOut,
    StepOut,
)
from app.services.approvals import LeadOptedOut
from app.services.audit import record
from app.services.deletion import StillWorking, delete_runs
from app.services.retry import NotFailed, retry_item

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
    return run_detail(db, get_or_404(db, Run, run_id, "run"))


POLL_SECONDS = 1.0
WAITING_POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 15.0
STREAM_MAX_SECONDS = 600.0
SETTLED = (RunStatus.COMPLETED, RunStatus.FAILED)


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        **error_docs(404),
        200: {"content": {"text/event-stream": {}}, "description": "`event: run` lines"},
    },
)
def run_events(run_id: int, request: Request, _user: CurrentUser, db: DbSession):
    """The run's detail as Server-Sent Events: `event: run` with the same JSON as
    `GET /api/runs/{id}` now and on every change, `event: gone` if it's deleted. Ends when
    the run finishes, or after 10 minutes (open a new one)."""
    get_or_404(db, Run, run_id, "run")
    stream = run_stream(request, run_id)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # no proxy buffering
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)


async def run_stream(request: Request, run_id: int) -> AsyncIterator[str]:
    factory = request.app.state.session_factory
    started = last_write = time.monotonic()
    seen: tuple | None = None
    while True:
        if await request.is_disconnected():
            return
        # The DB is sync: each look runs in a worker thread with its own short session.
        fingerprint, detail = await run_in_threadpool(look_at_run, factory, run_id, seen)
        if fingerprint is None:
            yield "event: gone\ndata: {}\n\n"
            return
        if detail is not None:
            seen = fingerprint
            yield f"event: run\ndata: {detail.model_dump_json()}\n\n"
            last_write = time.monotonic()
        status_now = fingerprint[0]
        if status_now in SETTLED or time.monotonic() - started >= STREAM_MAX_SECONDS:
            return
        if time.monotonic() - last_write >= HEARTBEAT_SECONDS:
            yield ": ping\n\n"  # keeps proxies from closing a quiet stream
            last_write = time.monotonic()
        waiting = status_now == RunStatus.WAITING_APPROVAL
        await asyncio.sleep(WAITING_POLL_SECONDS if waiting else POLL_SECONDS)


def look_at_run(factory: Any, run_id: int, seen: tuple | None) -> tuple:
    """(fingerprint, detail): the run's cheap fingerprint, plus its full detail when that
    changed since `seen`. (None, None) once the run is gone."""
    with factory() as db:
        run = db.get(Run, run_id)
        if run is None:
            return None, None
        count, newest = db.execute(
            select(func.count(RunItem.id), func.max(RunItem.updated_at)).where(
                RunItem.run_id == run_id
            )
        ).one()
        fingerprint = (run.status, run.started_at, run.finished_at, count, newest)
        if fingerprint == seen:
            return fingerprint, None
        return fingerprint, run_detail(db, run)


def run_detail(db: Session, run: Run) -> RunDetail:
    campaign = db.get(Campaign, run.campaign_id)
    rows = db.execute(run_items_query(run.id)).all()
    items = [item_row(item, name) for item, name in rows]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return RunDetail(**run_out(run, campaign.name).model_dump(), counts=counts, items=items)


@router.delete(
    "/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_docs(403, 404, 409)
)
def delete_run(run_id: int, admin: AdminUser, db: DbSession) -> Response:
    """Delete the run and its items, plus every lead that was in no other run (blanked in
    the audit log). 409 while the run is still collecting or the worker is on an item."""
    run = get_or_404(db, Run, run_id, "run")
    campaign_id = run.campaign_id
    try:
        deleted = delete_runs(db, [run])
    except StillWorking as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"{exc}; try again shortly") from exc
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="run.deleted",
        entity_type="run",
        entity_id=run_id,
        run_id=run_id,
        details={"campaign_id": campaign_id, **deleted.details()},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.post("/run-items/{item_id}/retry", responses=error_docs(403, 404, 409))
def retry_run_item(item_id: int, admin: AdminUser, db: DbSession) -> RunItemRow:
    """Queue a failed lead again; the worker continues it from where it stopped.

    409 if the item isn't failed (e.g. someone already retried it), or if it was approved
    and its lead has since opted out.
    """
    try:
        item = retry_item(db, item_id, admin, datetime.now(UTC))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run item {item_id} not found") from exc
    except (NotFailed, LeadOptedOut) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return item_row(item, db.get(Lead, item.lead_id).name)


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
    """Each item's rubric (its run's), score breakdown + explanation (its `score` step),
    whether its category was checked (its `check` step) and extraction issues (its
    `extract` step). An item that hasn't got that far gets a `Review`
    with only the rubric."""
    item_ids = list(item_ids)
    reviews = {item_id: Review() for item_id in item_ids}
    rubrics = db.execute(
        select(RunItem.id, ScoringRubric)
        .join(Run, Run.id == RunItem.run_id)
        .join(ScoringRubric, ScoringRubric.id == Run.rubric_id)
        .where(RunItem.id.in_(item_ids))
    )
    for item_id, rubric in rubrics:
        reviews[item_id].rubric = RubricRef(
            id=rubric.id, name=rubric.name, version=rubric.version, description=rubric.description
        )
    steps = db.scalars(
        select(RunStep)
        .where(
            RunStep.run_item_id.in_(item_ids),
            RunStep.node.in_(("score", "check", "extract")),
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
        elif step.node == "check":
            review.category_checked = output.get("category_checked")
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
