"""The API on real Postgres: two reviewers deciding at once, and the lead filters.

The approval transaction's row lock (`SELECT ... FOR UPDATE`) only really works on
Postgres (SQLite has no row locks), so this is where "two reviewers click at the same
moment -> exactly one wins" is tested:

- a race: for several items, two reviewers send their decision at the same instant
  (a thread barrier); every time one gets 200 and the other 409
- the lock itself: while another transaction holds the item's row lock, a decision
  waits; when that transaction has decided, the waiting one gets 409

The lead filters match the run items' JSON flags as text, which Postgres (JSONB) prints
differently from SQLite, so they're checked here too.

Skipped unless `TEST_POSTGRES_URL` is set (see `conftest.py` in this folder).
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Approval,
    AuditLog,
    Job,
    JobType,
    RunItem,
    RunItemStatus,
    User,
    UserRole,
)
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from tests.agent_world import World, add_lead
from tests.conftest import TEST_JWT_SECRET, add_user, auth_headers
from tests.migrations import alembic_config


@dataclass
class Api:
    client: TestClient
    db: Session
    world: World
    alice: User
    bob: User


@pytest.fixture
def api(postgres_url: str) -> Iterator[Api]:
    command.upgrade(alembic_config(postgres_url), "head")
    engine = make_engine(postgres_url)
    settings = Settings(_env_file=None, database_url=postgres_url, jwt_secret=TEST_JWT_SECRET)
    with make_session_factory(engine)() as db, TestClient(create_app(settings)) as client:
        world = World(db)
        alice = add_user(db, "alice@example.com", UserRole.REVIEWER)
        bob = add_user(db, "bob@example.com", UserRole.REVIEWER)
        yield Api(client, db, world, alice, bob)
    engine.dispose()


def waiting_items(api: Api, count: int) -> list[RunItem]:
    """`count` run items paused at the approval gate (as the worker would leave them)."""
    items = []
    for number in range(count):
        item = api.world.add_item(add_lead(api.db, f"Dental {number}"))
        item.status = RunItemStatus.AWAITING_APPROVAL
        items.append(item)
    api.db.commit()
    return items


def decide(api: Api, item_id: int, reviewer: User, body: dict) -> int:
    response = api.client.post(
        f"/api/run-items/{item_id}/decision", json=body, headers=auth_headers(reviewer)
    )
    return response.status_code


def send_together(
    barrier: threading.Barrier, api: Api, item_id: int, reviewer: User, body: dict
) -> int:
    barrier.wait()  # both requests leave at the same moment
    return decide(api, item_id, reviewer, body)


def count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_two_reviewers_at_once_exactly_one_wins(api: Api) -> None:
    items = waiting_items(api, 8)
    approve = {"decision": "approved"}
    reject = {"decision": "rejected", "reason": "not a fit"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        for item in items:
            barrier = threading.Barrier(2)
            alice = pool.submit(send_together, barrier, api, item.id, api.alice, approve)
            bob = pool.submit(send_together, barrier, api, item.id, api.bob, reject)
            assert sorted([alice.result(), bob.result()]) == [200, 409], item.id

    api.db.expire_all()
    ids = [item.id for item in items]
    assert count(api.db, Approval, Approval.run_item_id.in_(ids)) == len(items)
    assert count(api.db, Job, Job.type == JobType.RESUME_ITEM) == len(items)
    decisions = AuditLog.action.in_(("approval.approved", "approval.rejected"))
    assert count(api.db, AuditLog, decisions) == len(items)
    statuses = api.db.scalars(select(RunItem.status).where(RunItem.id.in_(ids)))
    assert set(statuses) == {RunItemStatus.RESUMING}


def test_a_decision_waits_for_the_row_lock_then_gets_409(api: Api, postgres_url: str) -> None:
    (item,) = waiting_items(api, 1)
    other_engine = make_engine(postgres_url)

    with make_session_factory(other_engine)() as other, ThreadPoolExecutor(1) as pool:
        # Another transaction (another reviewer's request, mid-way) holds the row lock.
        locked = other.get(RunItem, item.id, with_for_update=True)
        waiting = pool.submit(decide, api, item.id, api.bob, {"decision": "approved"})
        with pytest.raises(TimeoutError):
            waiting.result(timeout=1)  # blocked on the lock, not answered

        other.add(Approval(run_item_id=item.id, reviewer_id=api.alice.id, decision="approved"))
        locked.status = RunItemStatus.RESUMING
        other.commit()  # releases the lock

        assert waiting.result(timeout=10) == 409
    other_engine.dispose()

    api.db.expire_all()
    approval = api.db.scalars(select(Approval).where(Approval.run_item_id == item.id)).one()
    assert approval.reviewer_id == api.alice.id
    assert count(api.db, Job, Job.type == JobType.RESUME_ITEM) == 0


def test_lead_filters_on_postgres(api: Api) -> None:
    alpha, beta = waiting_items(api, 2)
    alpha.flags = ["possible_duplicate:12", "category_mismatch"]
    beta.flags = ["closed"]
    lead_alpha, lead_beta = alpha.lead_id, beta.lead_id
    other = add_lead(api.db, "Beta_Smiles 100%", score=90)
    api.db.commit()
    headers = auth_headers(api.alice)

    def ids(query: str) -> list[int]:
        response = api.client.get(f"/api/leads?{query}", headers=headers)
        assert response.status_code == 200, response.text
        return [lead["id"] for lead in response.json()["items"]]

    assert ids("flag=possible_duplicate") == [lead_alpha]
    assert ids("flag=category_mismatch") == [lead_alpha]
    assert ids("flag=closed") == [lead_beta]
    assert ids("flag=possible") == []
    assert ids("q=100%25") == [other.id]
    assert ids("q=a_s") == [other.id]
    assert ids("q=dental 1") == [lead_beta]  # ILIKE: any case
    assert ids("sort=-score")[0] == other.id  # the others have no score: last
    assert ids(f"campaign_id={api.world.campaign.id}&sort=name") == [lead_alpha, lead_beta]
