"""Kill the worker mid-run on real Postgres, start it again, and check nothing ran twice.

This is the crash-safety test from PLAN §13 (Phase 6). It uses the real pieces: the
Alembic schema (with the audit trigger), LangGraph's `PostgresSaver` checkpoints and
the job queue with `SKIP LOCKED`. The website, LLM, CRM and notifier are the fakes from
`tests.agent_world`, and the campaign's source is a small CSV file: no network.

A "kill" is a `Crash` (a BaseException, so no `except Exception` catches it): the job
stays `running`, and the checkpointer's connection pool is closed, like the process
dying. A "restart" opens a new checkpointer and a new graph on the same database.

Skipped unless `TEST_POSTGRES_URL` is set (see `conftest.py` in this folder).
"""

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.config import Settings
from app.db.models import (
    Job,
    JobStatus,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
    StepStatus,
    User,
    UserRole,
)
from app.db.session import make_engine, make_session_factory
from app.services.approvals import decide
from app.services.runs import create_run
from app.worker.handlers import Deps
from app.worker.main import Worker
from tests.agent_world import World
from tests.fake_web import SMILE, make_crawler
from tests.migrations import alembic_config, table_names

CSV = f"""name,phone,website,category
Smile Austin Dental,(512) 555-0142,{SMILE},dentist
Barton Creek Dentistry,(512) 555-0199,,dentist
Lone Star Smiles,(512) 555-0123,,dentist
"""


class Crash(BaseException):
    """Stands in for the worker process being killed."""


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def pg(postgres_url: str, tmp_path: Path) -> Iterator[tuple[str, Session, World]]:
    command.upgrade(alembic_config(postgres_url), "head")
    engine = make_engine(postgres_url)
    with make_session_factory(engine)() as db:
        world = World(db)
        csv_path = tmp_path / "leads.csv"
        csv_path.write_text(CSV)
        world.campaign.sources = ["csv"]
        world.campaign.source_options = {**world.campaign.source_options, "csv_path": str(csv_path)}
        db.add(User(email="rev@example.com", password_hash="x", role=UserRole.REVIEWER))
        db.commit()
        yield postgres_url, db, world
    engine.dispose()


def start_worker(stack: ExitStack, url: str, world: World, clock: Clock, worker_id: str) -> Worker:
    """A fresh "process": its own checkpointer (connection pool), graph and DB engine."""
    checkpointer = stack.enter_context(open_checkpointer(url))
    engine = make_engine(url)
    stack.callback(engine.dispose)
    deps = Deps(
        settings=Settings(_env_file=None, database_url=url),
        session_factory=make_session_factory(engine),
        graph=build_graph(checkpointer),
        llm=world.llm,
        crawler=make_crawler(world.web),
        crm=world.crm,
        notifier=world.notifier,
        sources_client=httpx2.Client(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(404))
        ),
        now=clock,
    )
    worker = Worker(deps, worker_id=worker_id)
    worker.stop()  # run() then only does its startup recovery and returns
    worker.run()
    return worker


def items(db: Session, run: Run) -> list[RunItem]:
    db.expire_all()
    return list(db.scalars(select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.id)))


def completed(db: Session, item: RunItem) -> list[str]:
    stmt = (
        select(RunStep.node)
        .where(RunStep.run_item_id == item.id, RunStep.status == StepStatus.COMPLETED)
        .order_by(RunStep.id)
    )
    return list(db.scalars(stmt))


def crash(*_args, **_kwargs):
    raise Crash


def test_a_killed_worker_resumes_and_every_side_effect_happens_once(pg) -> None:
    url, db, world = pg
    clock = Clock()
    run = create_run(db, world.campaign, clock())
    real_llm = world.llm.structured
    real_upsert = world.crm.upsert_lead

    # --- Life 1: collect, then die in the middle of the first lead's extract (LLM call).
    with ExitStack() as process:
        worker = start_worker(process, url, world, clock, "host-a-1")
        worker.run_once()  # start_run
        world.llm.structured = crash
        with pytest.raises(Crash):
            worker.run_once()
    assert db.scalars(select(Job).where(Job.status == JobStatus.RUNNING)).one().locked_by == (
        "host-a-1"
    )
    crawled_before_the_crash = len(world.web.requests)

    # --- Life 2: another machine takes over once the lock is 5 minutes old.
    world.llm.structured = real_llm
    clock.now += timedelta(minutes=6)
    with ExitStack() as process:
        worker = start_worker(process, url, world, clock, "host-b-1")
        worker.run_until_idle()
    assert {item.status for item in items(db, run)} == {RunItemStatus.AWAITING_APPROVAL}
    assert len(world.web.requests) == crawled_before_the_crash  # enrich didn't run again
    db.refresh(run)
    assert run.status == RunStatus.WAITING_APPROVAL

    # Decisions: approve two, reject one.
    reviewer = db.scalars(select(User)).one()
    smile, barton, lone_star = items(db, run)
    decide(db, smile.id, reviewer, "approved", None, clock())
    decide(db, barton.id, reviewer, "approved", None, clock())
    decide(db, lone_star.id, reviewer, "rejected", "not our area", clock())

    # --- Life 3: die in act, right after the CRM write for the first approved lead.
    def write_then_crash(key, fields):
        real_upsert(key, fields)
        raise Crash

    with ExitStack() as process:
        worker = start_worker(process, url, world, clock, "host-b-1")
        world.crm.upsert_lead = write_then_crash
        with pytest.raises(Crash):
            worker.run_once()

    # --- Life 4: the same container restarts and takes its own job back at once.
    world.crm.upsert_lead = real_upsert
    with ExitStack() as process:
        worker = start_worker(process, url, world, clock, "host-b-1")
        worker.run_until_idle()

    smile, barton, lone_star = items(db, run)
    assert [item.status for item in (smile, barton, lone_star)] == [RunItemStatus.DONE] * 3
    assert [item.route for item in (smile, barton, lone_star)] == ["notify", "notify", "skip"]
    db.refresh(run)
    assert run.status == RunStatus.COMPLETED

    # No completed node ran again, for any item.
    nodes = ["enrich", "extract", "check", "score", "approval_gate", "route", "act"]
    assert completed(db, smile) == nodes
    assert completed(db, barton) == nodes
    assert completed(db, lone_star) == nodes[:-1]  # rejected: no act
    twice = db.execute(
        select(RunStep.run_item_id, RunStep.node)
        .where(RunStep.status == StepStatus.COMPLETED)
        .group_by(RunStep.run_item_id, RunStep.node)
        .having(func.count() > 1)
    ).all()
    assert twice == []

    # Side effects exactly once: one CRM record and one message per approved lead.
    assert sorted(world.crm.records) == sorted([smile.thread_id, barton.thread_id])
    assert len(world.notifier.sent) == 2
    assert smile.notified_at is not None and barton.notified_at is not None


def test_the_checkpoint_tables_live_beside_the_alembic_schema(pg) -> None:
    url, _db, _world = pg

    with open_checkpointer(url):
        pass

    engine = make_engine(url)
    assert {"checkpoints", "checkpoint_writes", "checkpoint_blobs"} <= table_names(engine)
    engine.dispose()
    # `alembic check` (through env.py's include_object) sees no drift: it leaves LangGraph's
    # tables and their indexes alone instead of planning to drop them.
    command.check(alembic_config(url))
