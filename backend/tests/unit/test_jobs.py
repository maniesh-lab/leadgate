"""Tests for the job queue (`app.worker.jobs`) on SQLite."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus, JobType
from app.worker.jobs import claim_next, enqueue, mark_done, mark_failed, recover_stale

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def add(db: Session, run_id: int, at: datetime = NOW) -> Job:
    job = enqueue(db, JobType.START_RUN, {"run_id": run_id}, at)
    db.commit()
    return job


def test_claim_takes_the_oldest_due_job_and_locks_it(db: Session) -> None:
    later = add(db, 2, NOW - timedelta(seconds=5))
    first = add(db, 1, NOW - timedelta(seconds=10))
    add(db, 3, NOW + timedelta(minutes=1))  # not due yet

    job = claim_next(db, "worker-a", NOW)

    assert job.id == first.id
    assert (job.status, job.locked_by, job.attempts) == (JobStatus.RUNNING, "worker-a", 1)
    assert job.locked_at == NOW
    assert claim_next(db, "worker-a", NOW).id == later.id
    assert claim_next(db, "worker-a", NOW) is None  # only the future job is left


def test_done_clears_the_lock(db: Session) -> None:
    add(db, 1)
    job = claim_next(db, "w", NOW)

    mark_done(db, job)

    db.refresh(job)
    assert (job.status, job.locked_by, job.locked_at) == (JobStatus.DONE, None, None)


def test_a_failure_is_retried_with_a_growing_wait_then_fails_for_good(db: Session) -> None:
    add(db, 1)
    waits = []
    now = NOW
    for _ in range(2):
        job = claim_next(db, "w", now)
        assert mark_failed(db, job, "boom", now) is True
        assert job.status == JobStatus.QUEUED
        waits.append((job.run_after - now).total_seconds())
        assert claim_next(db, "w", now) is None  # not due yet
        now = job.run_after

    job = claim_next(db, "w", now)
    assert mark_failed(db, job, "boom again", now) is False

    assert waits == [2, 4]
    assert (job.status, job.attempts, job.last_error) == (JobStatus.FAILED, 3, "boom again")
    assert claim_next(db, "w", now + timedelta(hours=1)) is None


def test_stale_running_jobs_go_back_to_the_queue(db: Session) -> None:
    add(db, 1, NOW - timedelta(minutes=10))
    add(db, 2, NOW - timedelta(minutes=10))
    stale = claim_next(db, "dead-worker", NOW - timedelta(minutes=6))
    fresh = claim_next(db, "busy-worker", NOW - timedelta(minutes=1))

    assert recover_stale(db, NOW) == 1

    db.refresh(stale)
    db.refresh(fresh)
    assert (stale.status, stale.locked_by) == (JobStatus.QUEUED, None)
    assert fresh.status == JobStatus.RUNNING
    again = claim_next(db, "new-worker", NOW)
    assert (again.id, again.attempts) == (stale.id, 2)
