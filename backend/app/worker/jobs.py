"""The job queue: a plain `jobs` table in Postgres (no Celery, no Redis).

- `enqueue()` adds a job (the caller commits it together with the change that needs it,
  e.g. a new run and its `start_run` job)
- `claim_next()` takes the oldest due job and locks it for this worker
- `mark_done()` / `mark_failed()` finish it; a failure is retried after 2, 4, 8 ... s
  (`2 ** attempts`) until `max_attempts` (3), then the job is `failed`
- `recover_stale()` puts `running` jobs whose worker vanished (locked more than 5 min
  ago) back in the queue: that's what makes "kill the worker, start it again" work

The claim is one statement:

    UPDATE jobs SET status='running', locked_at=:now, locked_by=:worker, attempts=attempts+1
    WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= :now
                ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1)
    RETURNING *

`SKIP LOCKED` lets several workers claim at once without ever getting the same job.
SQLite (unit tests) has no row locks: SQLAlchemy leaves the `FOR UPDATE` out there, and
SQLite allows only one writer at a time anyway, so the same code works on both.

Used by `app.worker.main` (the loop) and by whoever creates work (`app.services.runs`,
`app.services.approvals`).
"""

from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus, JobType

log = structlog.get_logger(__name__)

# A `running` job locked longer ago than this belonged to a worker that died.
STALE_AFTER = timedelta(minutes=5)
# last_error is cut to this many characters.
MAX_ERROR = 2000


def enqueue(session: Session, job_type: JobType, payload: dict[str, Any], now: datetime) -> Job:
    """Add a queued job, due now. The caller commits."""
    job = Job(type=job_type, payload=payload, status=JobStatus.QUEUED, run_after=now)
    session.add(job)
    return job


def claim_next(session: Session, worker_id: str, now: datetime) -> Job | None:
    """Lock the oldest due job for `worker_id` and commit. None when nothing is due."""
    next_id = (
        select(Job.id)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= now)
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == next_id)
        .values(
            status=JobStatus.RUNNING,
            locked_at=now,
            locked_by=worker_id,
            attempts=Job.attempts + 1,
        )
        .returning(Job)
        # populate_existing: if this session already holds the Job object, refresh it
        # with the returned row instead of handing back its old values.
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    job = session.scalars(stmt).first()
    session.commit()
    return job


def mark_done(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE
    job.locked_at = None
    job.locked_by = None
    session.commit()


def mark_failed(session: Session, job: Job, error: str, now: datetime) -> bool:
    """Queue the job again with a wait, or fail it for good. True if it will be retried."""
    job.last_error = error[:MAX_ERROR]
    job.locked_at = None
    job.locked_by = None
    retry = job.attempts < job.max_attempts
    if retry:
        job.status = JobStatus.QUEUED
        job.run_after = now + timedelta(seconds=2**job.attempts)
    else:
        job.status = JobStatus.FAILED
    session.commit()
    log.warning(
        "job_failed", job_id=job.id, type=job.type, attempts=job.attempts, retry=retry, error=error
    )
    return retry


def recover_stale(session: Session, now: datetime) -> int:
    """Put jobs left `running` by a dead worker back in the queue. Returns how many."""
    stmt = (
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < now - STALE_AFTER)
        .values(status=JobStatus.QUEUED, locked_at=None, locked_by=None, run_after=now)
        .execution_options(synchronize_session=False)
    )
    count = session.execute(stmt).rowcount
    session.commit()
    if count:
        log.warning("stale_jobs_requeued", count=count)
    return count
