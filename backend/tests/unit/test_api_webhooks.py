"""Tests for the n8n webhook and the results callback, with the real worker. No network.

`n8n` (from `tests/unit/conftest.py`) is the fake n8n Webhook node the callback lands in.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db.models import AuditLog, Campaign, Job, JobStatus, JobType, Run, RunTrigger
from app.services.n8n import SECRET_HEADER
from tests.agent_world import World
from tests.fake_web import SMILE
from tests.unit.conftest import login, run_jobs

SECRET = "n8n-test-secret-" + "x" * 32
HEADERS = {SECRET_HEADER: SECRET}
URL = "/api/webhooks/n8n/runs"
CALLBACK = "http://n8n.test:5678/webhook/leadgate-results"
LEADS = [
    {
        "name": "Smile Austin Dental",
        "phone": "(512) 555-0142",
        "website": SMILE,
        "category": "dentist",
    },
    {"name": "Barton Creek Dentistry", "phone": 5125550199, "rating": 4.6, "category": "dentist"},
    {"phone": "no name: skipped"},
]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"n8n_webhook_secret": SECRET})


def actions(world: World) -> list[str]:
    return list(world.db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_the_webhook_needs_the_secret(client: TestClient, world: World) -> None:
    body = {"campaign_id": world.campaign.id}
    missing = client.post(URL, json=body)
    wrong = client.post(URL, json=body, headers={SECRET_HEADER: "nope"})

    assert (missing.status_code, wrong.status_code) == (401, 401)
    assert missing.json()["error"]["code"] == "unauthorized"
    assert world.db.scalars(select(Run).where(Run.trigger == RunTrigger.N8N)).first() is None


def test_the_webhook_is_off_without_the_setting(
    client: TestClient, world: World, settings: Settings
) -> None:
    client.app.state.settings = settings.model_copy(update={"n8n_webhook_secret": None})
    response = client.post(URL, json={"campaign_id": world.campaign.id}, headers=HEADERS)
    assert response.status_code == 503


def test_a_campaign_id_queues_a_run_for_n8n(client: TestClient, world: World) -> None:
    response = client.post(
        URL,
        json={"campaign_id": world.campaign.id, "callback_url": CALLBACK},
        headers={**HEADERS, "X-Request-ID": "n8n-exec-42"},
    )

    assert response.status_code == 202
    answer = response.json()
    assert answer["campaign_id"] == world.campaign.id and answer["status"] == "queued"
    run = world.db.get(Run, answer["run_id"])
    assert (run.trigger, run.callback_url, run.correlation_id) == (
        RunTrigger.N8N,
        CALLBACK,
        "n8n-exec-42",
    )
    job = world.db.scalars(select(Job).where(Job.type == JobType.START_RUN)).one()
    assert "leads" not in job.payload  # the campaign's own sources


def test_niche_and_location_find_the_campaign_in_any_case(client: TestClient, world: World) -> None:
    response = client.post(
        URL, json={"niche": "DENTIST", "location": "austin, tx"}, headers=HEADERS
    )
    assert response.json()["campaign_id"] == world.campaign.id


def test_a_new_niche_and_location_make_a_campaign(client: TestClient, world: World) -> None:
    response = client.post(URL, json={"niche": "gym", "location": "Denver, CO"}, headers=HEADERS)

    campaign = world.db.get(Campaign, response.json()["campaign_id"])
    assert campaign.name == "gym in Denver, CO (n8n)"
    assert campaign.sources == ["osm"]
    assert campaign.rubric_id == world.rubrics["general-b2b"].id
    created = world.db.scalars(select(AuditLog).where(AuditLog.action == "campaign.created")).one()
    assert (created.actor_type.value, created.actor) == ("system", "n8n")


def test_a_niche_osm_doesnt_know_needs_its_own_leads(client: TestClient, world: World) -> None:
    body = {"niche": "unicorn groomer", "location": "Austin, TX"}
    assert client.post(URL, json=body, headers=HEADERS).status_code == 422

    with_leads = client.post(URL, json={**body, "leads": LEADS[:1]}, headers=HEADERS)
    assert with_leads.status_code == 202
    campaign = world.db.get(Campaign, with_leads.json()["campaign_id"])
    assert campaign.sources == []  # n8n brings the list


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"niche": "dentist"},
        {"campaign_id": 1, "callback_url": "ftp://n8n/x"},
        {"campaign_id": 1, "leads": []},
    ],
)
def test_bad_bodies_are_refused(client: TestClient, world: World, body: dict) -> None:
    response = client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_and_archived_campaigns(client: TestClient, world: World) -> None:
    assert client.post(URL, json={"campaign_id": 999}, headers=HEADERS).status_code == 404
    world.campaign.is_archived = True
    world.db.commit()
    assert client.post(
        URL, json={"campaign_id": world.campaign.id}, headers=HEADERS
    ).status_code == (409)


def test_inline_leads_run_to_the_end_and_n8n_gets_the_summary(
    client: TestClient, world: World, worker, clock, reviewer, n8n
) -> None:
    body = {"campaign_id": world.campaign.id, "leads": LEADS, "callback_url": CALLBACK}
    run_id = client.post(URL, json=body, headers=HEADERS).json()["run_id"]

    assert run_jobs(worker, clock) == 3  # start_run + 2 process_item (the 3rd lead is bad)
    run = world.db.get(Run, run_id)
    world.db.refresh(run)
    assert run.stats["bad_rows"] == 1
    assert run.stats["new"] == 2

    as_reviewer = login(client, reviewer.email)
    queue = client.get("/api/approvals/queue", headers=as_reviewer).json()["items"]
    assert [item["lead"]["name"] for item in queue] == [lead["name"] for lead in LEADS[:2]]
    smile, barton = queue
    client.post(
        f"/api/run-items/{smile['run_item_id']}/decision",
        json={"decision": "approved"},
        headers=as_reviewer,
    )
    client.post(
        f"/api/run-items/{barton['run_item_id']}/decision",
        json={"decision": "rejected", "reason": "too far away"},
        headers=as_reviewer,
    )
    assert n8n.requests == []

    assert run_jobs(worker, clock) == 3  # 2 resume_item + send_callback
    (request,) = n8n.requests
    assert str(request.url) == CALLBACK
    assert request.headers[SECRET_HEADER] == SECRET
    summary = json.loads(request.content)
    assert (summary["run_id"], summary["status"]) == (run_id, "completed")
    assert [(lead["name"], lead["route"]) for lead in summary["leads"]] == [
        ("Smile Austin Dental", "notify"),
        ("Barton Creek Dentistry", "skip"),
    ]
    assert summary["leads"][0]["crm_record_id"] == "fake-crm-1"
    assert "phone" not in summary["leads"][0]  # no contact details in the callback
    assert actions(world)[-1] == "run.callback_sent"


def test_a_callback_n8n_refuses_is_retried_then_given_up(
    client: TestClient, world: World, worker, clock, n8n
) -> None:
    n8n.status = 500
    world.campaign.sources = []  # an empty run: completed at once, so the callback is due
    world.db.commit()
    body = {"campaign_id": world.campaign.id, "callback_url": CALLBACK}
    client.post(URL, json=body, headers=HEADERS)

    run_jobs(worker, clock)  # start_run + the first try
    for wait in (2, 4):  # the queue's retry waits
        clock.advance(wait)
        worker.run_until_idle()

    assert len(n8n.requests) == 3
    job = world.db.scalars(select(Job).where(Job.type == JobType.SEND_CALLBACK)).one()
    world.db.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "callback answered 500" in job.last_error
    assert actions(world)[-1] == "run.callback_failed"
