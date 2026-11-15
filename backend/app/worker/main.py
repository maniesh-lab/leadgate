"""The worker: claims one job at a time from the `jobs` table and runs it.

`python -m app.worker` (the compose `worker` service) runs `main()`:

1. on startup, jobs left `running` by a dead worker go back in the queue
   (`recover_stale()`), or fail for good if that was their last attempt
   (`fail_next_worn_out()` + `give_up()`, one commit each); checked again every minute,
   for other workers that died; on the public demo (DEMO_REVIEWER_EMAIL set) the same
   minute check refills its approval queue (`app.db.demo.refill_queue()`)
2. loop: claim the next due job → run its handler (renewing its lock every minute) →
   done, retry later, or give up
3. when the queue is empty, wait POLL_SECONDS and look again; after an error outside a
   job (the database unreachable, say), log it, wait ERROR_WAIT_SECONDS and go on
4. on SIGTERM / SIGINT (`docker compose stop`, Ctrl+C): finish the current job, then exit

`RUN_WORKER_IN_API=true` runs the same loop in a background thread of the API instead
(`app.main`), for free hosting with a single process.

With PUBLIC_DEMO=true every HTTP client refuses all requests (`app.outside_calls`).

The worker id is `<hostname>-<pid>`. In a container the pid is always 1, so a restarted
container has the same id and takes its own unfinished jobs back at once.
"""

import os
import signal
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.agents.llm import make_llm
from app.config import Settings, get_settings
from app.db.demo import refill_queue
from app.db.models import Job
from app.db.session import make_engine, make_session_factory
from app.enrichment.crawler import Crawler, make_crawler_client
from app.observability.logging import configure_logging
from app.observability.tracing import make_tracer
from app.services.crm import make_crm
from app.services.notify import make_notifier
from app.sources.base import make_http_client
from app.worker.handlers import HANDLERS, Deps, give_up
from app.worker.jobs import (
    claim_next,
    fail_alone,
    fail_next_worn_out,
    mark_done,
    mark_failed,
    recover_stale,
    renew_lock,
    still_ours,
)

log = structlog.get_logger(__name__)

POLL_SECONDS = 1.5
RECOVER_EVERY_SECONDS = 60
# After an error outside a job (the database was unreachable, say), wait this long.
ERROR_WAIT_SECONDS = 10
# How often a running job's lock is renewed; well inside `jobs.STALE_AFTER` (5 min).
HEARTBEAT_SECONDS = 60


class Worker:
    def __init__(self, deps: Deps, worker_id: str | None = None) -> None:
        self.deps = deps
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        # Set by stop(); also wakes the loop up early from its wait.
        self.stopping = threading.Event()
        self.last_recover: datetime | None = None

    def run(self) -> None:
        log.info("worker_started", worker_id=self.id)
        self.survive(self.recover_own)
        while not self.stopping.is_set():
            if not self.survive(self.run_pass):
                self.stopping.wait(ERROR_WAIT_SECONDS)
        log.info("worker_stopped", worker_id=self.id)

    def run_pass(self) -> None:
        if self.last_recover is None:
            self.recover_own()  # the startup recovery failed: try it again
        self.recover_now_and_then()
        if not self.run_once():
            self.stopping.wait(POLL_SECONDS)

    def survive(self, step: Callable[[], None]) -> bool:
        """Run `step`; an error (a lost DB connection, say) is logged instead of ending the
        loop, since in the API's thread (RUN_WORKER_IN_API) nobody would restart it."""
        try:
            step()
        except Exception:
            log.exception("worker_loop_error", worker_id=self.id)
            return False
        return True

    def recover_own(self) -> None:
        """At startup: also take back this worker's own unfinished jobs, whatever their age."""
        now = self.deps.now()
        with self.deps.session_factory() as session:
            self.recover(session, now, own_id=self.id)
        self.last_recover = now

    def run_once(self) -> bool:
        """Claim and run one job. False when nothing was due."""
        with self.deps.session_factory() as session:
            job = claim_next(session, self.id, self.deps.now())
            if job is None:
                return False
            self.handle(session, job)
        return True

    def run_until_idle(self, max_jobs: int = 1000) -> int:
        """Run jobs until none is due (tests and scripts). Returns how many ran."""
        ran = 0
        while ran < max_jobs and self.run_once():
            ran += 1
        return ran

    def handle(self, session: Session, job: Job) -> None:
        context = {"correlation_id": job.payload.get("correlation_id"), "job_id": job.id}
        with structlog.contextvars.bound_contextvars(**context):
            log.info("job_started", type=job.type, attempt=job.attempts)
            try:
                with self.heartbeat(job.id):
                    try:
                        HANDLERS[job.type](self.deps, session, job.payload)
                    except Exception:
                        session.rollback()  # free its row locks before waiting on the heartbeat
                        raise
            except Exception as exc:
                log.exception("job_error", type=job.type)
                error = f"{type(exc).__name__}: {exc}"
                if not still_ours(session, job, self.id):
                    return
                # Not committed yet: a job out of tries is saved failed together with its
                # item (or run), so a crash in between can't strand the item `running`.
                if mark_failed(session, job, error, self.deps.now(), commit=False):
                    session.commit()
                else:
                    give_up(self.deps, session, job)
                return
            if still_ours(session, job, self.id):
                mark_done(session, job)
                log.info("job_done", type=job.type)

    @contextmanager
    def heartbeat(self, job_id: int) -> Iterator[None]:
        """Renew the job's lock every HEARTBEAT_SECONDS while it runs, from a thread of its
        own: other workers take a job whose lock is older than STALE_AFTER for a dead
        worker's, and a collect or a slow LLM can run longer than that."""
        done = threading.Event()

        def beat() -> None:
            while not done.wait(HEARTBEAT_SECONDS):
                try:
                    with self.deps.session_factory() as session:
                        renew_lock(session, job_id, self.id, self.deps.now())
                except Exception:
                    log.exception("job_heartbeat_error", job_id=job_id)

        thread = threading.Thread(target=beat, name=f"job-{job_id}-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join()

    def recover_now_and_then(self) -> None:
        now = self.deps.now()
        if self.last_recover and (now - self.last_recover).total_seconds() < RECOVER_EVERY_SECONDS:
            return
        with self.deps.session_factory() as session:
            self.recover(session, now)
            if self.deps.settings.demo_reviewer_email:
                refill_queue(session, now)
        self.last_recover = now

    def recover(self, session: Session, now: datetime, own_id: str | None = None) -> None:
        """Clean up after dead workers: their jobs on a last attempt fail (each in one commit
        with its item's failure), the rest requeue."""
        # One job that can't be given up mustn't stop the rest (or the loop's claims).
        retry_later: list[int] = []
        while job := fail_next_worn_out(session, now, own_id, skip=retry_later):
            try:
                give_up(self.deps, session, job)
            except LookupError:  # its item or run is gone: nothing to mark, the job fails alone
                session.rollback()
                fail_alone(session, job.id)
            except Exception:  # e.g. a lock timeout: left as it is, tried again next time
                session.rollback()
                log.exception("give_up_failed", job_id=job.id)
                retry_later.append(job.id)
        recover_stale(session, now, own_id)

    def stop(self, *_signal_args: object) -> None:
        if not self.stopping.is_set():
            log.info("worker_stopping", worker_id=self.id, note="finishing the current job")
        self.stopping.set()


@contextmanager
def open_worker(settings: Settings, worker_id: str | None = None) -> Iterator[Worker]:
    """A worker with everything it needs opened (DB, checkpointer, LLM, HTTP clients)."""
    with ExitStack() as stack:
        engine = make_engine(settings.database_url)
        stack.callback(engine.dispose)
        checkpointer = stack.enter_context(open_checkpointer(settings.database_url))
        tracer = make_tracer(settings)
        stack.callback(tracer.close)  # sends the last spans to Langfuse
        llm = make_llm(settings, tracer)
        stack.callback(llm.close)
        # The public demo refuses every outside call (`app.outside_calls`).
        blocked = settings.public_demo
        crawler_client = stack.enter_context(
            make_crawler_client(settings.osm_user_agent, blocked=blocked)
        )
        # Airtable, Discord and the n8n callback (same User-Agent and timeout as the sources).
        outbound_client = stack.enter_context(
            make_http_client(settings.osm_user_agent, blocked=blocked)
        )
        deps = Deps(
            settings=settings,
            session_factory=make_session_factory(engine),
            graph=build_graph(checkpointer),
            llm=llm,
            crawler=Crawler(
                crawler_client, settings.osm_user_agent, max_pages=settings.enrich_max_pages
            ),
            # Airtable / Discord when their settings are set, else the in-memory fakes.
            crm=make_crm(settings, outbound_client),
            notifier=make_notifier(settings, outbound_client),
            sources_client=stack.enter_context(
                make_http_client(settings.osm_user_agent, blocked=blocked)
            ),
            callback_client=outbound_client,
            tracer=tracer,
        )
        yield Worker(deps, worker_id)


def main() -> int:
    settings = get_settings()
    configure_logging()
    with open_worker(settings) as worker:
        signal.signal(signal.SIGTERM, worker.stop)
        signal.signal(signal.SIGINT, worker.stop)
        worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
