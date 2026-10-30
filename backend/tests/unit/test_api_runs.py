"""The whole flow through the API, with the real worker running the jobs in between.

login -> create a campaign -> start a run -> (worker) -> the queue -> decide -> (worker)
-> the run is completed. Then the read endpoints: runs, run detail, run item detail.
The campaign's source is the two-lead CSV file from `tests/unit/conftest.py`; the LLM,
website, CRM and notifier are fakes. No network.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLog, Job, JobType
from tests.agent_world import World, add_lead
from tests.unit.conftest import login, run_jobs


def test_the_whole_flow_through_the_api(
    client: TestClient, world: World, worker, clock, reviewer, started
) -> None:
    as_reviewer = login(client, reviewer.email)
    run_id = started["run"]["id"]

    queue = client.get("/api/approvals/queue", headers=as_reviewer).json()
    assert queue["total"] == 2
    smile, barton = queue["items"]
    assert smile["lead"]["name"] == "Smile Austin Dental"
    assert (smile["run_id"], smile["campaign_name"]) == (run_id, "CSV dentists")
    assert smile["score"] == 60
    assert smile["lead"]["status"] == "in_review"
    assert [rule["rule"] for rule in smile["review"]["score_breakdown"]][:2] == [
        "has phone",
        "has email",
    ]
    assert smile["review"]["score_explanation"]
    assert client.get(f"/api/runs/{run_id}", headers=as_reviewer).json()["status"] == (
        "waiting_approval"
    )

    approve = {"decision": "approved"}
    url = f"/api/run-items/{smile['run_item_id']}/decision"
    decided = client.post(url, json=approve, headers=as_reviewer)
    assert decided.status_code == 200
    assert (decided.json()["decision"], decided.json()["reviewer_email"]) == (
        "approved",
        reviewer.email,
    )
    # A second decision on the same item is a conflict.
    again = client.post(url, json={"decision": "rejected", "reason": "x"}, headers=as_reviewer)
    assert again.status_code == 409

    barton_url = f"/api/run-items/{barton['run_item_id']}/decision"
    no_reason = client.post(barton_url, json={"decision": "rejected"}, headers=as_reviewer)
    assert no_reason.status_code == 422
    assert no_reason.json()["error"]["message"] == "a rejection needs a reason"
    rejected = client.post(
        barton_url, json={"decision": "rejected", "reason": "no website"}, headers=as_reviewer
    )
    assert rejected.status_code == 200
    assert client.get("/api/approvals/queue", headers=as_reviewer).json()["total"] == 0

    assert run_jobs(worker, clock) == 2  # 2 resume_item
    run = client.get(f"/api/runs/{run_id}", headers=as_reviewer).json()
    assert run["status"] == "completed"
    assert run["counts"] == {"done": 2}
    assert [(item["lead_name"], item["route"]) for item in run["items"]] == [
        ("Smile Austin Dental", "notify"),
        ("Barton Creek Dentistry", "skip"),
    ]
    assert len(world.crm.records) == 1 and len(world.notifier.sent) == 1

    # The decisions are in the audit log, with the run's correlation id.
    decisions = ("approval.approved", "approval.rejected")
    audit = world.db.scalars(select(AuditLog).where(AuditLog.action.in_(decisions))).all()
    assert {(row.action, row.actor) for row in audit} == {
        ("approval.approved", reviewer.email),
        ("approval.rejected", reviewer.email),
    }
    assert {row.correlation_id for row in audit} == {started["run"]["correlation_id"]}


def test_run_item_detail_has_the_timeline_and_the_decision(
    client: TestClient, worker, clock, reviewer, started, as_reviewer
) -> None:
    run = client.get(f"/api/runs/{started['run']['id']}", headers=as_reviewer).json()
    item_id = run["items"][0]["id"]
    client.post(
        f"/api/run-items/{item_id}/decision", json={"decision": "approved"}, headers=as_reviewer
    )
    run_jobs(worker, clock)

    detail = client.get(f"/api/run-items/{item_id}", headers=as_reviewer).json()
    assert detail["status"] == "done"
    assert detail["route"] == "notify"
    assert detail["notified_at"] is not None
    assert detail["lead"]["name"] == "Smile Austin Dental"
    assert detail["lead"]["status"] == "approved"
    assert [step["node"] for step in detail["steps"]] == [
        "enrich",
        "extract",
        "check",
        "score",
        "approval_gate",
        "route",
        "act",
    ]
    assert {step["status"] for step in detail["steps"]} == {"completed"}
    assert detail["review"]["score_breakdown"]
    assert detail["review"]["extraction_issues"] == []
    assert detail["approval"]["decision"] == "approved"
    assert detail["approval"]["reviewer_email"] == reviewer.email

    assert client.get("/api/run-items/999", headers=as_reviewer).status_code == 404


def test_runs_list_filters_and_pages(client: TestClient, world: World, started, as_reviewer):
    second = client.post(
        f"/api/campaigns/{started['campaign']['id']}/runs", headers=started["as_admin"]
    ).json()

    listed = client.get("/api/runs", headers=as_reviewer).json()
    # Newest first; the oldest is the World's own run (from its fixture campaign).
    assert [run["id"] for run in listed["items"]] == [second["id"], started["run"]["id"], 1]
    assert listed["items"][0]["campaign_name"] == "CSV dentists"
    campaign_id = started["campaign"]["id"]
    queued = client.get(f"/api/runs?status=queued&campaign_id={campaign_id}", headers=as_reviewer)
    queued = queued.json()
    assert [run["id"] for run in queued["items"]] == [second["id"]]
    other = client.get("/api/runs?campaign_id=999", headers=as_reviewer).json()
    assert other["total"] == 0
    page = client.get("/api/runs?page=2&page_size=1", headers=as_reviewer).json()
    assert page["total"] == 3
    assert [run["id"] for run in page["items"]] == [started["run"]["id"]]
    assert client.get("/api/runs/999", headers=as_reviewer).status_code == 404


def test_queue_filters_by_campaign(
    client: TestClient, world: World, worker, clock, started, as_reviewer
) -> None:
    campaign_id = started["campaign"]["id"]
    mine = client.get(f"/api/approvals/queue?campaign_id={campaign_id}", headers=as_reviewer)
    assert mine.json()["total"] == 2
    other = client.get(f"/api/approvals/queue?campaign_id={world.campaign.id}", headers=as_reviewer)
    assert other.json()["total"] == 0


def test_deciding_an_item_not_at_the_gate_is_a_conflict(
    client: TestClient, world: World, as_reviewer
) -> None:
    item = world.add_item(add_lead(world.db))  # pending: the worker hasn't run it yet
    response = client.post(
        f"/api/run-items/{item.id}/decision", json={"decision": "approved"}, headers=as_reviewer
    )
    assert response.status_code == 409
    assert response.json()["error"]["message"] == f"run item {item.id} is pending"
    assert world.db.scalars(select(Job).where(Job.type == JobType.RESUME_ITEM)).all() == []
    missing = client.post(
        "/api/run-items/999/decision", json={"decision": "approved"}, headers=as_reviewer
    )
    assert missing.status_code == 404


def test_admins_decide_too_and_only_logged_in_users_read(
    client: TestClient, started, as_reviewer
) -> None:
    queue = client.get("/api/approvals/queue", headers=started["as_admin"])
    assert queue.status_code == 200
    item_id = queue.json()["items"][0]["run_item_id"]
    response = client.post(
        f"/api/run-items/{item_id}/decision",
        json={"decision": "approved"},
        headers=started["as_admin"],
    )
    assert response.status_code == 200
    for path in ("/api/runs", f"/api/runs/{started['run']['id']}", "/api/approvals/queue"):
        assert client.get(path).status_code == 401
