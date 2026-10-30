"""Fixtures for unit tests that run the real worker (the worker tests and the API tests).

`world` is `tests.agent_world.World` with a two-lead CSV file as the campaign's only
source; `worker` runs jobs with the real graph on the in-memory checkpointer and the
fakes (website, LLM, CRM, notifier), on a `clock` the test moves by hand. No network.
`started` is a run started through the API and taken to the approval gate by the worker.
`n8n` is a fake n8n Webhook node: the worker's results callbacks land in it.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import make_session_factory
from app.worker.handlers import Deps
from app.worker.main import Worker
from tests.agent_world import World
from tests.conftest import PASSWORD
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


class FakeN8n:
    """Answers the results callback with `status` and remembers each request."""

    def __init__(self) -> None:
        self.status = 200
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json={"message": "Workflow was started"})


@pytest.fixture
def n8n() -> FakeN8n:
    return FakeN8n()


@pytest.fixture
def deps(world: World, engine: Engine, settings: Settings, clock: Clock, n8n: FakeN8n) -> Deps:
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
        callback_client=httpx2.Client(transport=httpx2.MockTransport(n8n)),
        now=clock,
    )


@pytest.fixture
def worker(deps: Deps) -> Worker:
    return Worker(deps, worker_id="test-worker")


def run_jobs(worker, clock: Clock) -> int:
    """Run every due job. The API stamps jobs with the real time, so catch the clock up."""
    clock.now = max(clock.now, datetime.now(UTC) + timedelta(seconds=1))
    return worker.run_until_idle()


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def started(client: TestClient, world: World, worker, clock, admin, reviewer) -> dict:
    """A run started through the API and taken to the approval gate by the worker."""
    as_admin = login(client, admin.email)
    campaign = client.post(
        "/api/campaigns",
        json={
            "name": "CSV dentists",
            "niche": "dentist",
            "location": "Austin, TX",
            "sources": ["csv"],
            "source_options": {"csv_path": world.campaign.source_options["csv_path"]},
            "rubric_id": world.rubrics["general-b2b"].id,
        },
        headers=as_admin,
    ).json()
    run = client.post(f"/api/campaigns/{campaign['id']}/runs", headers=as_admin).json()
    assert run_jobs(worker, clock) == 3  # start_run + 2 process_item
    return {"campaign": campaign, "run": run, "as_admin": as_admin}
