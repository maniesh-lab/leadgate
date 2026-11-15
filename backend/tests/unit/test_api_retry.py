"""Tests for retrying a failed lead (`POST /api/run-items/{id}/retry`) with the real worker.

A lead fails when its job gives up after 3 tries. The retry puts it back on the queue;
the worker continues from the thread's last checkpoint, so nothing that already
finished runs again and the CRM write / message still happen once.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

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
)
from app.services.approvals import decide
from tests.agent_world import World
from tests.unit.conftest import run_jobs
from tests.unit.test_worker import broken_llm, completed_steps, items, reviewer, start


def fail_every_try(worker, clock) -> None:
    """Run the due jobs through all 3 tries (each retry waits a little)."""
    for _ in range(3):
        worker.run_until_idle()
        clock.advance(60)
    worker.run_until_idle()


def retry(client: TestClient, item: RunItem, headers: dict):
    return client.post(f"/api/run-items/{item.id}/retry", headers=headers)


@pytest.fixture
def failed_run(world: World, worker, clock) -> Run:
    """A run whose two leads both failed in `extract` (the LLM was down)."""
    run = start(world, clock)
    worker.run_once()  # start_run
    world.llm.structured = broken_llm
    fail_every_try(worker, clock)
    del world.llm.structured  # the LLM is back
    world.db.refresh(run)
    assert run.status == RunStatus.COMPLETED
    return run


def test_a_lead_that_failed_before_the_gate_continues_from_its_checkpoint(
    client: TestClient, world: World, worker, clock, failed_run: Run, as_admin
) -> None:
    smile = items(world.db, failed_run)[0]
    assert smile.status == RunItemStatus.FAILED
    assert completed_steps(world.db, smile) == ["enrich"]

    response = retry(client, smile, as_admin)

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["error"]) == ("pending", None)
    world.db.expire_all()
    run = world.db.get(Run, failed_run.id)
    assert (run.status, run.finished_at) == (RunStatus.RUNNING, None)  # reopened
    [audit] = world.db.scalars(select(AuditLog).where(AuditLog.action == "run_item.retried"))
    assert (audit.run_item_id, audit.run_id) == (smile.id, run.id)
    assert audit.details["job"] == "process_item"
    assert audit.details["previous_error"] == "RuntimeError: LLM down"
    assert audit.correlation_id == "corr-run"

    # A second click finds it pending: 409, and no second job.
    again = retry(client, smile, as_admin)
    assert again.status_code == 409
    assert again.json()["error"]["message"] == f"run item {smile.id} is pending, not failed"
    queued = Job.status == JobStatus.QUEUED
    assert len(world.db.scalars(select(Job).where(queued)).all()) == 1

    assert run_jobs(worker, clock) == 1
    smile = items(world.db, failed_run)[0]
    assert smile.status == RunItemStatus.AWAITING_APPROVAL
    # enrich finished before the failure: it isn't run again.
    assert completed_steps(world.db, smile) == ["enrich", "extract", "check", "score"]
    # Smile waits for a human; Barton is still failed (it doesn't keep the run busy).
    assert world.db.get(Run, failed_run.id).status == RunStatus.WAITING_APPROVAL


def test_an_approved_lead_that_failed_in_act_is_sent_to_the_crm_once(
    client: TestClient, world: World, worker, clock, as_admin
) -> None:
    run = start(world, clock)
    worker.run_until_idle()  # both leads reach the gate
    smile = items(world.db, run)[0]
    decide(world.db, smile.id, reviewer(world.db), "approved", None, clock())
    real_upsert = world.crm.upsert_lead

    def crm_down(key, fields, record_id=None):
        raise RuntimeError("CRM down")

    world.crm.upsert_lead = crm_down
    fail_every_try(worker, clock)
    smile = items(world.db, run)[0]
    assert (smile.status, smile.error) == (RunItemStatus.FAILED, "RuntimeError: CRM down")
    world.crm.upsert_lead = real_upsert

    response = retry(client, smile, as_admin)
    assert response.json()["status"] == "resuming"
    assert (
        world.db.scalars(
            select(AuditLog.details).where(AuditLog.action == "run_item.retried")
        ).one()["job"]
        == "resume_item"
    )
    run_jobs(worker, clock)

    smile = items(world.db, run)[0]
    assert (smile.status, smile.route) == (RunItemStatus.DONE, "notify")
    assert list(world.crm.records) == [smile.thread_id]
    assert len(world.notifier.sent) == 1
    for node in ("enrich", "extract", "score", "approval_gate", "act"):
        assert completed_steps(world.db, smile).count(node) == 1, node


def test_only_admins_retry_and_only_failed_items(
    client: TestClient, world: World, worker, clock, failed_run: Run, as_reviewer, as_admin
) -> None:
    smile = items(world.db, failed_run)[0]
    assert retry(client, smile, as_reviewer).status_code == 403
    assert client.post(f"/api/run-items/{smile.id}/retry").status_code == 401
    assert client.post("/api/run-items/999/retry", headers=as_admin).status_code == 404
    assert (
        world.db.scalars(select(AuditLog).where(AuditLog.action == "run_item.retried")).all() == []
    )


def test_an_approved_item_of_an_opted_out_lead_cant_be_retried(
    client: TestClient, world: World, worker, clock, as_admin
) -> None:
    run = start(world, clock)
    worker.run_until_idle()
    smile = items(world.db, run)[0]
    decide(world.db, smile.id, reviewer(world.db), "approved", None, clock())
    world.crm.upsert_lead = lambda key, fields: (_ for _ in ()).throw(RuntimeError("down"))
    fail_every_try(worker, clock)
    smile = items(world.db, run)[0]
    world.db.get(Lead, smile.lead_id).opt_out = True
    world.db.commit()

    response = retry(client, smile, as_admin)

    assert response.status_code == 409
    assert "opted out" in response.json()["error"]["message"]
    assert items(world.db, run)[0].status == RunItemStatus.FAILED
    assert not world.db.scalars(
        select(Job).where(Job.type == JobType.RESUME_ITEM, Job.status == JobStatus.QUEUED)
    ).all()
