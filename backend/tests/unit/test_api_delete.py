"""Deleting leads, runs, campaigns and uploaded CSVs (`app.services.deletion`).

`started` is a run of a two-lead CSV campaign, both leads waiting at the approval gate.
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    ActorType,
    AuditLog,
    Campaign,
    CsvUpload,
    Job,
    JobStatus,
    JobType,
    Lead,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
)
from app.services.audit import record
from app.services.deletion import redact_audit
from tests.agent_world import World
from tests.unit.conftest import CSV, run_jobs


def message(response) -> str:
    return response.json()["error"]["message"]


def waiting(client: TestClient, headers: dict) -> list[dict]:
    return client.get("/api/approvals/queue", headers=headers).json()["items"]


def audit_about(world: World, lead_id: int) -> list[AuditLog]:
    world.db.expire_all()
    return list(
        world.db.scalars(
            select(AuditLog).where(AuditLog.entity_type == "lead", AuditLog.entity_id == lead_id)
        )
    )


def test_deleting_a_lead_keeps_its_audit_rows_but_blanks_them(
    client: TestClient, world: World, started, as_reviewer
) -> None:
    first, second = waiting(client, as_reviewer)
    lead_id, item_id = first["lead"]["id"], first["run_item_id"]
    name = first["lead"]["name"]
    rows_before = len(audit_about(world, lead_id))
    assert name in json.dumps([row.details for row in audit_about(world, lead_id)])

    response = client.delete(f"/api/leads/{lead_id}", headers=started["as_admin"])

    assert response.status_code == 204
    assert client.get(f"/api/leads/{lead_id}", headers=as_reviewer).status_code == 404
    assert client.get(f"/api/run-items/{item_id}", headers=as_reviewer).status_code == 404
    assert world.db.scalars(select(RunStep).where(RunStep.run_item_id == item_id)).all() == []
    # The rows stay (who did what, and when), without the name or the source data.
    rows = audit_about(world, lead_id)
    kept = [row for row in rows if row.action != "lead.deleted"]
    assert len(kept) == rows_before
    assert name not in json.dumps([row.details for row in rows])
    assert all(row.details["redacted"] == "lead deleted" for row in kept)
    scored = next(row for row in kept if row.action == "lead.scored")
    assert "score" in scored.details and "score_explanation" not in scored.details
    deleted = next(row for row in rows if row.action == "lead.deleted")
    assert (deleted.actor, deleted.details["run_items"], deleted.details["leads"]) == (
        "admin@example.com",
        1,
        1,
    )
    # The other lead is untouched and still waits.
    assert [item["run_item_id"] for item in waiting(client, as_reviewer)] == [second["run_item_id"]]
    assert second["lead"]["name"] in json.dumps(
        [row.details for row in audit_about(world, second["lead"]["id"])]
    )


def test_deleting_the_last_waiting_lead_completes_the_run(
    client: TestClient, world: World, started, as_reviewer
) -> None:
    for item in waiting(client, as_reviewer):
        client.delete(f"/api/leads/{item['lead']['id']}", headers=started["as_admin"])

    run = client.get(f"/api/runs/{started['run']['id']}", headers=as_reviewer).json()
    assert (run["status"], run["items"]) == (RunStatus.COMPLETED, [])


def test_only_admins_delete_and_unknown_ids_are_404(
    client: TestClient, started, as_reviewer
) -> None:
    lead_id = waiting(client, as_reviewer)[0]["lead"]["id"]
    run_id, campaign_id = started["run"]["id"], started["campaign"]["id"]
    for path in (f"leads/{lead_id}", f"runs/{run_id}", f"campaigns/{campaign_id}"):
        assert client.delete(f"/api/{path}", headers=as_reviewer).status_code == 403
        assert (
            client.delete(f"/api/{path.split('/')[0]}/999", headers=started["as_admin"]).status_code
            == 404
        )


def test_a_lead_the_worker_is_on_is_not_deleted(
    client: TestClient, world: World, started, as_reviewer
) -> None:
    item_id = waiting(client, as_reviewer)[0]["run_item_id"]
    item = world.db.get(RunItem, item_id)
    item.status = RunItemStatus.RESUMING
    world.db.commit()

    response = client.delete(f"/api/leads/{item.lead_id}", headers=started["as_admin"])

    assert response.status_code == 409
    assert "still resuming" in message(response)
    world.db.expire_all()
    assert world.db.get(Lead, item.lead_id) is not None


def test_deleting_a_run_takes_its_leads_unless_another_run_has_them(
    client: TestClient, world: World, worker, clock, started, as_reviewer
) -> None:
    as_admin = started["as_admin"]
    first_run = started["run"]["id"]
    # A second run of the same campaign finds the same two leads.
    second_run = client.post(
        f"/api/campaigns/{started['campaign']['id']}/runs", headers=as_admin
    ).json()["id"]
    run_jobs(worker, clock)
    lead_ids = {item["lead"]["id"] for item in waiting(client, as_reviewer)}
    assert len(lead_ids) == 2

    response = client.delete(f"/api/runs/{first_run}", headers=as_admin)

    assert response.status_code == 204
    assert client.get(f"/api/runs/{first_run}", headers=as_reviewer).status_code == 404
    world.db.expire_all()
    assert set(world.db.scalars(select(Lead.id))) == lead_ids  # still in the second run
    assert (
        world.db.scalars(
            select(Job.id).where(Job.payload["run_id"].as_integer() == first_run)
        ).all()
        == []
    )
    deleted = world.db.scalars(select(AuditLog).where(AuditLog.action == "run.deleted")).one()
    assert deleted.details["run_items"] == 2 and deleted.details["leads"] == 0

    # Now the second run is the leads' last one: they go with it.
    assert client.delete(f"/api/runs/{second_run}", headers=as_admin).status_code == 204
    world.db.expire_all()
    assert world.db.scalars(select(Lead.id)).all() == []
    assert world.db.get(Campaign, started["campaign"]["id"]) is not None


def test_deleting_a_run_keeps_its_opted_out_leads_out(
    client: TestClient, world: World, worker, clock, started, as_reviewer
) -> None:
    as_admin = started["as_admin"]
    opted_out, other = (item["lead"]["id"] for item in waiting(client, as_reviewer))
    assert client.delete(f"/api/leads/{opted_out}/data", headers=as_admin).status_code == 204
    run_jobs(worker, clock)  # its rejected item finishes; the other lead still waits

    assert client.delete(f"/api/runs/{started['run']['id']}", headers=as_admin).status_code == 204
    world.db.expire_all()
    assert world.db.get(Lead, other) is None
    assert world.db.get(Lead, opted_out).opt_out is True  # the row that keeps it out stays

    # A new run finds the other lead again, but never the opted-out one.
    client.post(f"/api/campaigns/{started['campaign']['id']}/runs", headers=as_admin)
    run_jobs(worker, clock)
    world.db.expire_all()
    assert world.db.scalars(select(RunItem.lead_id).where(RunItem.lead_id == opted_out)).all() == []
    assert len(waiting(client, as_reviewer)) == 1


def test_an_opted_out_lead_isnt_deleted(
    client: TestClient, world: World, worker, clock, started, as_reviewer
) -> None:
    as_admin = started["as_admin"]
    lead_id = waiting(client, as_reviewer)[0]["lead"]["id"]
    client.delete(f"/api/leads/{lead_id}/data", headers=as_admin)
    run_jobs(worker, clock)

    response = client.delete(f"/api/leads/{lead_id}", headers=as_admin)

    assert response.status_code == 409
    assert "opted out" in message(response)
    world.db.expire_all()
    assert world.db.get(Lead, lead_id).opt_out is True  # the row that keeps it out stays


def test_a_run_the_worker_has_not_started_is_not_deleted(
    client: TestClient, world: World, started
) -> None:
    as_admin = started["as_admin"]
    queued = client.post(
        f"/api/campaigns/{started['campaign']['id']}/runs", headers=as_admin
    ).json()["id"]

    response = client.delete(f"/api/runs/{queued}", headers=as_admin)

    assert response.status_code == 409
    assert "job queued for this run" in message(response)
    assert (
        client.delete(f"/api/campaigns/{started['campaign']['id']}", headers=as_admin).status_code
        == 409
    )
    world.db.expire_all()
    assert world.db.get(Run, queued) is not None


def test_a_run_still_collecting_is_not_deleted(client: TestClient, world: World, started) -> None:
    run = world.db.get(Run, started["run"]["id"])
    run.status = RunStatus.COLLECTING
    world.db.commit()

    response = client.delete(f"/api/runs/{run.id}", headers=started["as_admin"])

    assert response.status_code == 409
    assert "still collecting leads" in message(response)


def test_a_lead_with_a_queued_job_is_not_deleted(
    client: TestClient, world: World, started, as_reviewer
) -> None:
    item = waiting(client, as_reviewer)[0]
    # Like a decision the worker hasn't picked up yet: the item still waits, its job doesn't.
    world.db.add(
        Job(
            type=JobType.RESUME_ITEM,
            payload={"run_item_id": item["run_item_id"]},
            status=JobStatus.QUEUED,
        )
    )
    world.db.commit()

    response = client.delete(f"/api/leads/{item['lead']['id']}", headers=started["as_admin"])

    assert response.status_code == 409
    assert "job queued for one of these leads" in message(response)


def test_a_queued_run_whose_job_is_gone_can_be_deleted(
    client: TestClient, world: World, started
) -> None:
    as_admin = started["as_admin"]
    queued = client.post(
        f"/api/campaigns/{started['campaign']['id']}/runs", headers=as_admin
    ).json()["id"]
    for job in world.db.scalars(select(Job).where(Job.payload["run_id"].as_integer() == queued)):
        world.db.delete(job)
    world.db.commit()

    response = client.delete(f"/api/runs/{queued}", headers=as_admin)

    assert response.status_code == 204
    world.db.expire_all()
    assert world.db.get(Run, queued) is None


def test_deleting_a_campaign_takes_its_runs_leads_and_file(
    client: TestClient, world: World, started, as_reviewer
) -> None:
    campaign_id = started["campaign"]["id"]
    upload_id = started["campaign"]["source_options"]["csv_upload_id"]

    response = client.delete(f"/api/campaigns/{campaign_id}", headers=started["as_admin"])

    assert response.status_code == 204
    world.db.expire_all()
    assert world.db.get(Campaign, campaign_id) is None
    assert world.db.get(CsvUpload, upload_id) is None
    assert world.db.scalars(select(Run.id).where(Run.campaign_id == campaign_id)).all() == []
    assert world.db.scalars(select(Lead.id)).all() == []
    assert waiting(client, as_reviewer) == []
    deleted = world.db.scalars(select(AuditLog).where(AuditLog.action == "campaign.deleted")).one()
    assert deleted.details == {
        "name": "CSV dentists",
        "campaigns": 1,
        "runs": 1,
        "run_items": 2,
        "leads": 2,
        "csv_uploads": 1,
    }


def test_an_uploaded_file_in_use_is_not_deleted(client: TestClient, world: World, started) -> None:
    as_admin = started["as_admin"]
    in_use = started["campaign"]["source_options"]["csv_upload_id"]

    response = client.delete(f"/api/uploads/csv/{in_use}", headers=as_admin)

    assert response.status_code == 409
    assert f"campaign {started['campaign']['id']}" in message(response)

    spare = client.post(
        "/api/uploads/csv",
        params={"filename": "spare.csv"},
        content=CSV,
        headers={**as_admin, "Content-Type": "text/csv"},
    ).json()["id"]
    assert client.delete(f"/api/uploads/csv/{spare}", headers=as_admin).status_code == 204
    assert client.delete(f"/api/uploads/csv/{spare}", headers=as_admin).status_code == 404
    world.db.expire_all()
    assert world.db.get(CsvUpload, spare) is None
    assert world.db.scalars(
        select(AuditLog).where(AuditLog.action == "csv.deleted")
    ).one().details == {
        "filename": "spare.csv",
        "campaigns": 0,
        "runs": 0,
        "run_items": 0,
        "leads": 0,
        "csv_uploads": 1,
    }


def test_redact_audit_keeps_only_the_safe_details(world: World) -> None:
    row = record(
        world.db,
        actor_type=ActorType.USER,
        actor="reviewer@example.com",
        action="approval.rejected",
        entity_type="run_item",
        entity_id=7,
        run_item_id=7,
        details={"lead_id": 3, "reason": "Smile Austin Dental is closed", "score": 40},
    )
    other = record(
        world.db,
        actor_type=ActorType.AGENT,
        actor="extract",
        action="lead.extracted",
        entity_type="lead",
        entity_id=4,
        details={"cleaned": {"name": "Barton Creek Dentistry"}},
    )
    world.db.flush()

    assert redact_audit(world.db, [3], [7], "lead deleted") == 1

    assert row.details == {"lead_id": 3, "score": 40, "redacted": "lead deleted"}
    assert row.actor == "reviewer@example.com"  # only the details change
    assert other.details == {"cleaned": {"name": "Barton Creek Dentistry"}}
