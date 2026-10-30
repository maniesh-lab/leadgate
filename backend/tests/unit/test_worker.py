"""Tests for the worker (`app.worker.main` + `app.worker.handlers`) on SQLite.

The real graph runs on the in-memory checkpointer with the fakes from `tests.agent_world`;
the campaign's only source is a small CSV file, so nothing touches the network. A
"crash" is a `Crash` (a BaseException, like the process being killed): it skips every
`except Exception` and leaves the job `running`, exactly like a killed worker.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    AuditLog,
    Job,
    JobStatus,
    JobType,
    Lead,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
    User,
    UserRole,
)
from app.db.session import make_session_factory
from app.services.approvals import decide
from app.services.runs import create_run
from app.worker import handlers
from app.worker.handlers import Deps
from app.worker.jobs import enqueue
from app.worker.main import Worker, open_worker
from tests.agent_world import World
from tests.fake_web import SMILE, make_crawler

CSV = f"""name,phone,website,category
Smile Austin Dental,(512) 555-0142,{SMILE},dentist
Barton Creek Dentistry,(512) 555-0199,,dentist
"""


class Crash(BaseException):
    """Stands in for the worker process being killed."""


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def world(db: Session, tmp_path: Path) -> World:
    world = World(db)
    csv_path = tmp_path / "leads.csv"
    csv_path.write_text(CSV)
    world.campaign.sources = ["csv"]
    world.campaign.source_options = {**world.campaign.source_options, "csv_path": str(csv_path)}
    db.commit()
    return world


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def deps(world: World, engine: Engine, settings: Settings, clock: Clock) -> Deps:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    return Deps(
        settings=settings,
        session_factory=make_session_factory(engine),
        graph=world.graph,
        llm=world.llm,
        crawler=make_crawler(world.web),
        crm=world.crm,
        notifier=world.notifier,
        sources_client=httpx2.Client(transport=httpx2.MockTransport(refuse)),
        now=clock,
    )


@pytest.fixture
def worker(deps: Deps) -> Worker:
    return Worker(deps, worker_id="test-worker")


def start(world: World, clock: Clock) -> Run:
    return create_run(world.db, world.campaign, clock(), correlation_id="corr-run")


def items(db: Session, run: Run) -> list[RunItem]:
    db.expire_all()
    return list(db.scalars(select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.id)))


def completed_steps(db: Session, item: RunItem) -> list[str]:
    stmt = (
        select(RunStep.node)
        .where(RunStep.run_item_id == item.id, RunStep.status == "completed")
        .order_by(RunStep.id)
    )
    return list(db.scalars(stmt))


def reviewer(db: Session) -> User:
    user = User(email="rev@example.com", password_hash="x", role=UserRole.REVIEWER)
    db.add(user)
    db.commit()
    return user


def test_a_run_goes_from_collect_to_the_queue_to_completed(world, worker, clock) -> None:
    run = start(world, clock)

    assert worker.run_until_idle() == 3  # start_run + 2 process_item

    smile, barton = items(world.db, run)
    assert {smile.status, barton.status} == {RunItemStatus.AWAITING_APPROVAL}
    assert smile.score == 60 and smile.extraction_ok is True
    assert completed_steps(world.db, smile) == ["enrich", "extract", "check", "score"]
    world.db.refresh(run)
    assert run.status == RunStatus.WAITING_APPROVAL

    user = reviewer(world.db)
    decide(world.db, smile.id, user, "approved", None, clock())
    decide(world.db, barton.id, user, "rejected", "no website", clock())
    assert worker.run_until_idle() == 2  # 2 resume_item

    smile, barton = items(world.db, run)
    assert (smile.status, smile.route) == (RunItemStatus.DONE, "notify")
    assert (barton.status, barton.route) == (RunItemStatus.DONE, "skip")
    assert smile.notified_at is not None
    assert completed_steps(world.db, smile)[4:] == ["approval_gate", "route", "act"]
    assert len(world.crm.records) == 1
    assert len(world.notifier.sent) == 1
    world.db.refresh(run)
    assert run.status == RunStatus.COMPLETED
    assert world.db.get(Lead, smile.lead_id).status == "approved"
    audit = world.db.scalars(select(AuditLog).where(AuditLog.action == "run.completed")).one()
    assert audit.correlation_id == "corr-run"


def test_a_failing_job_is_retried_then_its_item_fails(world, worker, clock) -> None:
    run = start(world, clock)
    worker.run_once()  # start_run
    world.llm.structured = broken_llm

    for _ in range(3):
        assert worker.run_once()  # each try fails in extract
        worker.run_until_idle()
        clock.advance(60)  # past the retry wait
    worker.run_until_idle()

    jobs = world.db.scalars(select(Job).where(Job.type == JobType.PROCESS_ITEM)).all()
    assert {job.status for job in jobs} == {JobStatus.FAILED}
    assert {job.attempts for job in jobs} == {3}
    failed = items(world.db, run)
    assert {item.status for item in failed} == {RunItemStatus.FAILED}
    assert failed[0].error == "RuntimeError: LLM down"
    world.db.refresh(run)
    assert run.status == RunStatus.COMPLETED  # nothing left to wait for


def test_a_start_run_that_keeps_failing_fails_the_run(world, worker, clock) -> None:
    world.campaign.source_options = {}  # the csv source needs csv_path
    world.db.commit()
    run = start(world, clock)

    for _ in range(3):
        worker.run_once()
        clock.advance(60)

    world.db.refresh(run)
    assert run.status == RunStatus.FAILED
    assert "every source failed" in run.stats["error"]


def test_a_killed_worker_is_restarted_and_finished_nodes_do_not_run_again(
    world, deps, clock
) -> None:
    run = start(world, clock)
    first = Worker(deps, worker_id="container-1")
    first.run_once()  # start_run
    # Kill the worker in the middle of the first lead's extract (its LLM call).
    real_structured = world.llm.structured
    world.llm.structured = crash
    with pytest.raises(Crash):
        first.run_once()
    stuck = world.db.scalars(select(Job).where(Job.status == JobStatus.RUNNING)).one()
    assert stuck.locked_by == "container-1"
    requests_before_restart = len(world.web.requests)

    # The same container starts again: its own job comes back at once.
    world.llm.structured = real_structured
    restarted = Worker(deps, worker_id="container-1")
    restarted.stop()  # only the startup recovery, then return
    restarted.run()
    restarted.run_until_idle()

    smile = items(world.db, run)[0]
    assert smile.status == RunItemStatus.AWAITING_APPROVAL
    assert completed_steps(world.db, smile) == ["enrich", "extract", "check", "score"]
    # enrich finished before the crash, so the site was not crawled again.
    assert len(world.web.requests) == requests_before_restart


def test_a_crash_in_act_is_finished_on_restart_with_one_crm_record(world, deps, clock):
    run = start(world, clock)
    first = Worker(deps, worker_id="container-1")
    first.run_until_idle()
    smile = items(world.db, run)[0]
    decide(world.db, smile.id, reviewer(world.db), "approved", None, clock())
    real_upsert = world.crm.upsert_lead

    def crash_after_writing(key, fields):
        real_upsert(key, fields)
        raise Crash

    world.crm.upsert_lead = crash_after_writing
    with pytest.raises(Crash):
        first.run_once()

    world.crm.upsert_lead = real_upsert
    restarted = Worker(deps, worker_id="container-1")
    restarted.stop()
    restarted.run()
    restarted.run_until_idle()

    smile = items(world.db, run)[0]
    assert smile.status == RunItemStatus.DONE
    assert list(world.crm.records) == [smile.thread_id]  # written twice, one record
    assert len(world.notifier.sent) == 1
    assert completed_steps(world.db, smile).count("act") == 1


def test_stop_lets_the_current_job_finish_then_the_loop_ends(
    world, worker, clock, monkeypatch
) -> None:
    run = start(world, clock)
    enqueue(world.db, JobType.START_RUN, {"run_id": run.id}, clock())  # a second job
    world.db.commit()
    real_start_run = handlers.HANDLERS[JobType.START_RUN]

    def start_run_then_sigterm(deps, session, payload):
        worker.stop()  # SIGTERM arrives in the middle of the job
        real_start_run(deps, session, payload)

    monkeypatch.setitem(handlers.HANDLERS, JobType.START_RUN, start_run_then_sigterm)

    worker.run()

    starts = world.db.scalars(select(Job).where(Job.type == JobType.START_RUN).order_by(Job.id))
    assert [job.status for job in starts] == [JobStatus.DONE, JobStatus.QUEUED]
    # The items' jobs it queued are still waiting too: the loop ended after one job.
    processes = world.db.scalars(select(Job).where(Job.type == JobType.PROCESS_ITEM))
    assert {job.status for job in processes} == {JobStatus.QUEUED}


def test_a_job_type_without_a_handler_fails(world, worker, clock) -> None:
    enqueue(world.db, JobType.SEND_CALLBACK, {"run_id": 1}, clock())
    world.db.commit()

    for _ in range(3):
        worker.run_once()
        clock.advance(60)

    job = world.db.scalars(select(Job)).one()
    assert job.status == JobStatus.FAILED
    assert "no handler for send_callback" in job.last_error


def test_a_lead_that_opted_out_before_processing_is_skipped(world, worker, clock) -> None:
    run = start(world, clock)
    worker.run_once()  # start_run
    smile = items(world.db, run)[0]
    world.db.get(Lead, smile.lead_id).opt_out = True
    world.db.commit()

    worker.run_until_idle()

    smile = items(world.db, run)[0]
    assert smile.status == RunItemStatus.DONE
    assert "opted out" in smile.error
    assert completed_steps(world.db, smile) == []


def test_a_job_for_an_item_already_done_does_nothing(world, worker, clock) -> None:
    run = start(world, clock)
    worker.run_until_idle()
    smile = items(world.db, run)[0]
    enqueue(world.db, JobType.PROCESS_ITEM, {"run_item_id": smile.id}, clock())
    enqueue(world.db, JobType.RESUME_ITEM, {"run_item_id": smile.id}, clock())
    world.db.commit()
    steps_before = len(world.db.scalars(select(RunStep)).all())

    assert worker.run_until_idle() == 2

    assert len(world.db.scalars(select(RunStep)).all()) == steps_before
    assert items(world.db, run)[0].status == RunItemStatus.AWAITING_APPROVAL


def test_open_worker_builds_a_worker_from_the_settings(settings: Settings, engine) -> None:
    # On SQLite the checkpointer is the in-memory one; LLM_MODE defaults to fake.
    with open_worker(settings, worker_id="from-settings") as worker:
        worker.stop()
        worker.run()

    assert worker.id == "from-settings"
    assert worker.last_recover is not None


def broken_llm(*_args, **_kwargs):
    raise RuntimeError("LLM down")


def crash(*_args, **_kwargs):
    raise Crash
