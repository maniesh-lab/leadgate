"""Fixtures for unit tests that run the real worker (the worker tests and the API tests).

`world` is `tests.agent_world.World` with a two-lead CSV file as the campaign's only
source; `worker` runs jobs with the real graph on the in-memory checkpointer and the
fakes (website, LLM, CRM, notifier), on a `clock` the test moves by hand. No network.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import make_session_factory
from app.worker.handlers import Deps
from app.worker.main import Worker
from tests.agent_world import World
from tests.fake_web import SMILE, make_crawler

CSV = f"""name,phone,website,category
Smile Austin Dental,(512) 555-0142,{SMILE},dentist
Barton Creek Dentistry,(512) 555-0199,,dentist
"""


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
