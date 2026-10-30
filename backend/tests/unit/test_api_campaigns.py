"""Tests for `/api/campaigns` and starting a run through the API."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Campaign, Job, JobType, Run, ScoringRubric


def body(rubric: ScoringRubric, **changes) -> dict:
    return {
        "name": "Austin dentists",
        "niche": "dentist",
        "location": "Austin, TX",
        "sources": ["osm"],
        "rubric_id": rubric.id,
        **changes,
    }


def test_admin_creates_a_campaign_everyone_can_read(
    client: TestClient, db: Session, rubric, as_admin, as_reviewer
) -> None:
    response = client.post(
        "/api/campaigns",
        json=body(rubric, name="  Austin dentists ", sources=["osm", "osm"]),
        headers=as_admin,
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Austin dentists"
    assert created["sources"] == ["osm"]
    assert created["source_options"] == {}
    assert created["is_archived"] is False

    assert client.get(f"/api/campaigns/{created['id']}", headers=as_reviewer).status_code == 200
    listed = client.get("/api/campaigns", headers=as_reviewer).json()
    assert [c["id"] for c in listed["items"]] == [created["id"]]
    assert db.scalars(select(AuditLog.action)).all() == ["campaign.created"]


def test_campaign_checks(client: TestClient, rubric, as_admin) -> None:
    def status(**changes) -> int:
        return client.post(
            "/api/campaigns", json=body(rubric, **changes), headers=as_admin
        ).status_code

    assert status(rubric_id=999) == 422
    assert status(niche="unicorn tamer") == 422  # OSM has no tags for it
    assert status(niche="unicorn tamer", sources=["google_places"]) == 201  # fine without OSM
    assert status(sources=["csv"]) == 422  # no csv_path
    assert status(sources=["csv"], source_options={"csv_path": "/data/x.csv"}) == 201
    assert status(sources=[]) == 422
    assert status(sources=["yelp"]) == 422
    assert status(source_options={"max_result": 5}) == 422  # typo'd key
    assert status(source_options={"max_results": 0}) == 422
    assert status(name="   ") == 422


def test_reviewer_cant_create_edit_or_run(client: TestClient, db: Session, rubric, as_reviewer):
    campaign = Campaign(
        name="c", niche="dentist", location="Austin", sources=["osm"], rubric_id=rubric.id
    )
    db.add(campaign)
    db.commit()
    assert client.post("/api/campaigns", json=body(rubric), headers=as_reviewer).status_code == 403
    patch = client.patch(f"/api/campaigns/{campaign.id}", json={"name": "x"}, headers=as_reviewer)
    assert patch.status_code == 403
    run = client.post(f"/api/campaigns/{campaign.id}/runs", headers=as_reviewer)
    assert run.status_code == 403


def test_patch_changes_only_what_is_sent(client: TestClient, db: Session, rubric, as_admin):
    created = client.post("/api/campaigns", json=body(rubric), headers=as_admin).json()
    campaign = db.get(Campaign, created["id"])
    campaign.source_options = {"osm_area": {"query": "Austin, TX"}, "max_results": 10}
    db.commit()

    response = client.patch(
        f"/api/campaigns/{created['id']}",
        json={"location": "Dallas, TX", "source_options": {"max_results": 20}},
        headers=as_admin,
    )
    assert response.status_code == 200
    updated = response.json()
    assert (updated["name"], updated["location"]) == ("Austin dentists", "Dallas, TX")
    # The OSM cache stays (the source sees it's for another location and looks it up again).
    assert updated["source_options"] == {"max_results": 20, "osm_area": {"query": "Austin, TX"}}

    audit = db.scalars(select(AuditLog).where(AuditLog.action == "campaign.updated")).one()
    assert audit.details == {"location": "Dallas, TX", "source_options": {"max_results": 20}}


def test_patch_refuses_nulls_and_bad_values(client: TestClient, rubric, as_admin) -> None:
    created = client.post("/api/campaigns", json=body(rubric), headers=as_admin).json()
    url = f"/api/campaigns/{created['id']}"
    assert client.patch(url, json={"name": None}, headers=as_admin).status_code == 422
    assert client.patch(url, json={"rubric_id": 999}, headers=as_admin).status_code == 422
    assert client.patch(url, json={"niche": "unicorn tamer"}, headers=as_admin).status_code == 422
    assert client.patch("/api/campaigns/999", json={}, headers=as_admin).status_code == 404


def test_archived_campaigns_are_listed_apart_and_cant_run(
    client: TestClient, rubric, as_admin
) -> None:
    created = client.post("/api/campaigns", json=body(rubric), headers=as_admin).json()
    url = f"/api/campaigns/{created['id']}"
    client.patch(url, json={"is_archived": True}, headers=as_admin)

    assert client.get("/api/campaigns", headers=as_admin).json()["total"] == 0
    archived = client.get("/api/campaigns?archived=true", headers=as_admin).json()
    assert archived["total"] == 1
    run = client.post(f"{url}/runs", headers=as_admin)
    assert run.status_code == 409
    assert run.json()["error"]["code"] == "conflict"


def test_start_run_queues_the_run_and_its_job(
    client: TestClient, db: Session, admin, rubric, as_admin
) -> None:
    created = client.post("/api/campaigns", json=body(rubric), headers=as_admin).json()
    response = client.post(
        f"/api/campaigns/{created['id']}/runs", headers={**as_admin, "X-Request-ID": "req-42"}
    )
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "queued"
    assert (run["trigger"], run["triggered_by"], run["rubric_id"]) == (
        "manual",
        admin.id,
        rubric.id,
    )
    assert run["correlation_id"] == "req-42"  # the request's id follows the run

    job = db.scalars(select(Job)).one()
    assert job.type == JobType.START_RUN
    assert job.payload == {"run_id": run["id"], "correlation_id": "req-42"}
    assert db.get(Run, run["id"]).campaign_id == created["id"]
    assert client.post("/api/campaigns/999/runs", headers=as_admin).status_code == 404
