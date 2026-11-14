"""Tests for the worker (`app.worker.main` + `app.worker.handlers`) on SQLite.

The real graph runs on the in-memory checkpointer with the fakes from `tests.agent_world`;
the campaign's only source is a small CSV file, so nothing touches the network. A
"crash" is a `Crash` (a BaseException, like the process being killed): it skips every
`except Exception` and leaves the job `running`, exactly like a killed worker.
"""

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.agents import runner
from app.config import Settings
from app.db.models import (
    AuditLog,
    Job,
    JobStatus,
    JobType,
    Lead,
    LeadStatus,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
    User,
    UserRole,
)
from app.main import create_app
from app.services.approvals import decide
from app.services.privacy import opt_out_lead
from app.services.runs import create_run
from app.worker import handlers
from app.worker import main as worker_main
from app.worker.jobs import enqueue, recover_stale
from app.worker.main import Worker, open_worker
from tests.agent_world import World
from tests.unit.conftest import Clock


class Crash(BaseException):
    """Stands in for the worker process being killed."""


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
    world.campaign.source_options = {}  # the csv source needs a file
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


def test_jobs_that_kill_the_worker_every_time_fail_their_items(world, deps, clock) -> None:
    run = start(world, clock)
    Worker(deps, worker_id="container-1").run_once()  # start_run
    world.llm.structured = crash  # every lead's extract now kills the worker

    for _ in range(100):  # each round: the container restarts, then tries one job
        restarted = Worker(deps, worker_id="container-1")
        restarted.stop()  # only the startup recovery, then return
        restarted.run()
        try:
            if not restarted.run_once():
                break
        except Crash:
            pass
    else:
        pytest.fail("the jobs kept coming back")

    jobs = world.db.scalars(select(Job).where(Job.type == JobType.PROCESS_ITEM)).all()
    assert {(job.status, job.attempts) for job in jobs} == {(JobStatus.FAILED, 3)}
    failed = items(world.db, run)
    assert {item.status for item in failed} == {RunItemStatus.FAILED}
    assert failed[0].error == "the worker stopped during attempt 3 of 3 (killed or out of memory)"
    world.db.refresh(run)
    assert run.status == RunStatus.COMPLETED  # nothing left to wait for


def test_a_crash_in_act_is_finished_on_restart_with_one_crm_record(world, deps, clock):
    run = start(world, clock)
    first = Worker(deps, worker_id="container-1")
    first.run_until_idle()
    smile = items(world.db, run)[0]
    decide(world.db, smile.id, reviewer(world.db), "approved", None, clock())
    real_upsert = world.crm.upsert_lead

    def crash_after_writing(key, fields, record_id=None):
        real_upsert(key, fields, record_id)
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


def test_an_opt_out_during_the_crawl_is_not_undone(world, worker, clock, deps) -> None:
    run = start(world, clock)
    worker.run_once()  # start_run
    smile = items(world.db, run)[0]
    crawl = deps.crawler.crawl

    def opt_out_meanwhile(website: str):
        with deps.session_factory() as other:  # the admin, in another request
            opt_out_lead(other, other.get(Lead, smile.lead_id))
            other.commit()
        return crawl(website)

    deps.crawler.crawl = opt_out_meanwhile
    worker.run_until_idle()

    world.db.expire_all()
    lead = world.db.get(Lead, smile.lead_id)
    assert lead.opt_out is True
    assert (lead.phone, lead.email, lead.website, lead.enrichment) == (None, None, None, None)
    assert lead.services == []


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


def test_run_worker_in_api_runs_jobs_in_a_background_thread(world, settings) -> None:
    world.campaign.sources = []  # nothing to collect: the start_run job completes the run
    world.db.commit()
    run = create_run(world.db, world.campaign, datetime.now(UTC))
    settings = settings.model_copy(update={"run_worker_in_api": True})

    with TestClient(create_app(settings)):
        for _ in range(100):  # up to 5 s
            world.db.expire_all()
            if world.db.get(Run, run.id).status == RunStatus.COMPLETED:
                break
            time.sleep(0.05)

    assert world.db.get(Run, run.id).status == RunStatus.COMPLETED


def broken_llm(*_args, **_kwargs):
    raise RuntimeError("LLM down")


def crash(*_args, **_kwargs):
    raise Crash


def test_a_database_error_outside_a_job_does_not_end_the_loop(
    world, worker, clock, monkeypatch
) -> None:
    run = start(world, clock)
    real_claim = worker_main.claim_next
    calls = 0

    def flaky_claim(session, worker_id, now):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("SELECT", {}, Exception("server closed the connection"))
        job = real_claim(session, worker_id, now)
        if job is None:
            worker.stop()  # the queue is empty: end the test's loop
        return job

    monkeypatch.setattr(worker_main, "claim_next", flaky_claim)
    monkeypatch.setattr(worker_main, "ERROR_WAIT_SECONDS", 0)

    worker.run()

    assert {item.status for item in items(world.db, run)} == {RunItemStatus.AWAITING_APPROVAL}


def test_a_slow_job_keeps_its_lock_so_no_other_worker_takes_it(
    world, worker, clock, deps, monkeypatch
) -> None:
    start(world, clock)
    monkeypatch.setattr(worker_main, "HEARTBEAT_SECONDS", 0.01)
    requeued = []

    def slow_start_run(_deps, session, payload):
        clock.advance(10 * 60)  # far past STALE_AFTER
        deadline = time.monotonic() + 5
        with deps.session_factory() as other:
            # Wait (in real time) for the heartbeat to renew the lock at the new time.
            while other.get(Job, job_id(other), populate_existing=True).locked_at < clock():
                assert time.monotonic() < deadline, "the lock was never renewed"
                time.sleep(0.01)
            requeued.append(recover_stale(other, clock()))  # another worker's minute check

    monkeypatch.setitem(handlers.HANDLERS, JobType.START_RUN, slow_start_run)
    worker.run_once()

    assert requeued == [0]
    world.db.expire_all()
    assert world.db.get(Job, job_id(world.db)).status == JobStatus.DONE


def test_a_job_another_worker_took_over_is_left_to_it(world, worker, clock, monkeypatch) -> None:
    start(world, clock)

    def taken_over(_deps, session, payload):
        # This worker lost its lock (say it couldn't renew it), and another took the job.
        session.execute(update(Job).values(locked_by="other-worker"))
        session.commit()

    monkeypatch.setitem(handlers.HANDLERS, JobType.START_RUN, taken_over)
    worker.run_once()

    world.db.expire_all()
    job = world.db.get(Job, job_id(world.db))
    assert (job.status, job.locked_by) == (JobStatus.RUNNING, "other-worker")


def job_id(session: Session) -> int:
    return session.scalars(select(Job.id).where(Job.type == JobType.START_RUN)).one()


def test_a_kill_after_the_gate_before_the_lead_was_saved_still_saves_it(
    world, deps, clock, monkeypatch
) -> None:
    run = start(world, clock)
    first = Worker(deps, worker_id="container-1")
    first.run_once()  # start_run
    real_save = runner.save_result

    def killed_before_saving(*args, **kwargs):
        monkeypatch.setattr(runner, "save_result", real_save)
        raise Crash

    monkeypatch.setattr(runner, "save_result", killed_before_saving)
    with pytest.raises(Crash):
        first.run_once()  # the first lead's thread reaches the gate, then the kill

    restarted = Worker(deps, worker_id="container-1")
    restarted.stop()  # only the startup recovery, then return
    restarted.run()
    restarted.run_until_idle()

    smile = items(world.db, run)[0]
    lead = world.db.get(Lead, smile.lead_id)
    assert smile.status == RunItemStatus.AWAITING_APPROVAL
    assert (lead.status, lead.score) == (LeadStatus.IN_REVIEW, smile.score)
    assert lead.phone == "+15125550142"  # the extracted, cleaned phone


def test_a_decision_for_a_thread_whose_state_is_gone_fails_the_item(world, worker, clock) -> None:
    run = start(world, clock)
    worker.run_until_idle()
    smile = items(world.db, run)[0]
    score = smile.score
    decide(world.db, smile.id, reviewer(world.db), "approved", None, clock())
    world.graph.checkpointer.delete_thread(smile.thread_id)  # deleted meanwhile

    for _ in range(3):  # each attempt fails; then the job gives up
        clock.advance(60)
        worker.run_until_idle()

    smile = items(world.db, run)[0]
    assert smile.status == RunItemStatus.FAILED
    assert "no saved state to resume" in smile.error
    assert smile.score == score  # not wiped


def worn_out_job(world, run, item, status: RunItemStatus, clock) -> Job:
    """`item` at `status`, and its process job left running on its last attempt by a worker
    that died long ago."""
    item.status = status
    job = enqueue(world.db, JobType.PROCESS_ITEM, {"run_item_id": item.id}, clock())
    job.status, job.attempts, job.max_attempts = JobStatus.RUNNING, 3, 3
    job.locked_by, job.locked_at = "dead-worker", clock()
    world.db.commit()
    clock.advance(10 * 60)
    return job


def test_a_worn_out_job_leaves_an_item_it_already_finished_alone(world, worker, clock) -> None:
    # Killed between saving the item (at the gate) and marking the job done, three times.
    run = start(world, clock)
    worker.run_once()  # start_run: the items and their jobs
    smile = items(world.db, run)[0]
    job = worn_out_job(world, run, smile, RunItemStatus.AWAITING_APPROVAL, clock)

    with worker.deps.session_factory() as session:
        worker.recover(session, clock())

    world.db.expire_all()
    assert world.db.get(Job, job.id).status == JobStatus.FAILED
    assert items(world.db, run)[0].status == RunItemStatus.AWAITING_APPROVAL


def test_a_worn_out_job_and_its_item_fail_in_one_commit(world, worker, clock, monkeypatch) -> None:
    run = start(world, clock)
    worker.run_once()
    smile = items(world.db, run)[0]
    job = worn_out_job(world, run, smile, RunItemStatus.RUNNING, clock)
    real_finish = handlers.finish_item

    def broken(*_args, **_kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(handlers, "finish_item", broken)
    with worker.deps.session_factory() as session, pytest.raises(RuntimeError):
        worker.recover(session, clock())
    world.db.expire_all()
    # Neither was saved: the job is still there to be failed by the next recovery.
    assert world.db.get(Job, job.id).status == JobStatus.RUNNING

    monkeypatch.setattr(handlers, "finish_item", real_finish)
    with worker.deps.session_factory() as session:
        worker.recover(session, clock())
    world.db.expire_all()
    assert world.db.get(Job, job.id).status == JobStatus.FAILED
    assert items(world.db, run)[0].status == RunItemStatus.FAILED


def test_a_lead_approved_again_in_a_later_run_stays_one_crm_record(world, worker, clock) -> None:
    first_run = start(world, clock)
    worker.run_until_idle()
    smile = items(world.db, first_run)[0]
    who = reviewer(world.db)
    decide(world.db, smile.id, who, "approved", None, clock())
    worker.run_until_idle()
    record_id = world.db.get(Lead, smile.lead_id).crm_record_id
    assert record_id is not None

    second_run = start(world, clock)  # the same campaign again finds the same leads
    worker.run_until_idle()
    again = next(i for i in items(world.db, second_run) if i.lead_id == smile.lead_id)
    decide(world.db, again.id, who, "approved", None, clock())
    worker.run_until_idle()

    assert world.crm.ids == {again.thread_id: record_id}  # one record, moved to run 2
    world.db.expire_all()
    assert world.db.get(Lead, smile.lead_id).crm_record_id == record_id
