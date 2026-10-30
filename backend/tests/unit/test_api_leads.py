"""Tests for `/api/leads`: filters, sort, search, pages, the detail and the opt-out."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLog, Lead, LeadStatus, Run, RunItem, RunItemStatus, RunTrigger
from app.services.approvals import NotAwaitingDecision
from tests.agent_world import World, add_lead
from tests.unit.conftest import run_jobs


def names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [lead["name"] for lead in response.json()["items"]]


def leads_world(world: World) -> dict[str, Lead]:
    """Four leads with different scores, statuses, areas and emails; two in world.run."""
    db = world.db
    leads = {
        "alpha": add_lead(db, "Alpha Dental", score=80, email="a@alpha.test", area="Zilker"),
        "beta": add_lead(db, "Beta_Smiles 100%", score=20, area="zilker"),
        "gamma": add_lead(db, "Gamma Dental", score=None, status=LeadStatus.APPROVED),
        "delta": add_lead(db, "Delta Orthodontics", score=55, email="d@delta.test"),
    }
    db.commit()
    alpha = world.add_item(leads["alpha"])
    alpha.flags = ["possible_duplicate:4", "category_mismatch"]
    beta = world.add_item(leads["beta"])
    beta.flags = ["closed"]
    db.commit()
    return leads


def test_filters(client: TestClient, world: World, as_reviewer) -> None:
    leads_world(world)

    def get(query: str) -> list[str]:
        return sorted(names(client.get(f"/api/leads?{query}", headers=as_reviewer)))

    assert get("") == ["Alpha Dental", "Beta_Smiles 100%", "Delta Orthodontics", "Gamma Dental"]
    assert get("status=approved") == ["Gamma Dental"]
    assert get(f"campaign_id={world.campaign.id}") == ["Alpha Dental", "Beta_Smiles 100%"]
    assert get("campaign_id=999") == []
    assert get("area=ZILKER") == ["Alpha Dental", "Beta_Smiles 100%"]
    assert get("min_score=50") == ["Alpha Dental", "Delta Orthodontics"]
    assert get("min_score=50&max_score=60") == ["Delta Orthodontics"]
    assert get("has_email=true") == ["Alpha Dental", "Delta Orthodontics"]
    assert get("has_email=false") == ["Beta_Smiles 100%", "Gamma Dental"]
    assert get("flag=possible_duplicate") == ["Alpha Dental"]
    assert get("flag=closed") == ["Beta_Smiles 100%"]
    assert get("flag=possible") == []  # whole flag names only
    assert get("q=dental") == ["Alpha Dental", "Gamma Dental"]
    # % and _ are plain characters in a search, not wildcards.
    assert get("q=100%25") == ["Beta_Smiles 100%"]
    assert get("q=a_s") == ["Beta_Smiles 100%"]
    assert get("q=a%25s") == []
    assert get("has_email=true&q=delta") == ["Delta Orthodontics"]


def test_list_shows_each_leads_latest_flags(client: TestClient, world: World, as_reviewer) -> None:
    leads = leads_world(world)
    newer_run = Run(
        campaign_id=world.campaign.id, trigger=RunTrigger.MANUAL, rubric_id=world.campaign.rubric_id
    )
    world.db.add(newer_run)
    world.db.commit()
    world.db.add(
        RunItem(run_id=newer_run.id, lead_id=leads["alpha"].id, thread_id="newer", flags=["closed"])
    )
    world.db.commit()

    page = client.get("/api/leads?sort=name", headers=as_reviewer).json()
    assert {lead["name"]: lead["flags"] for lead in page["items"]} == {
        "Alpha Dental": ["closed"],  # from the newer run, not the first one
        "Beta_Smiles 100%": ["closed"],
        "Delta Orthodontics": [],  # never in a run
        "Gamma Dental": [],
    }
    detail = client.get(f"/api/leads/{leads['alpha'].id}", headers=as_reviewer).json()
    assert detail["flags"] == ["closed"]


def test_bad_filters_are_refused(client: TestClient, world: World, as_reviewer) -> None:
    for query in ("min_score=101", "flag=Closed!", "sort=random", "status=hot", "q="):
        response = client.get(f"/api/leads?{query}", headers=as_reviewer)
        assert response.status_code == 422, query


def test_sort_and_pages(client: TestClient, world: World, as_reviewer) -> None:
    leads_world(world)

    def get(query: str) -> list[str]:
        return names(client.get(f"/api/leads?{query}", headers=as_reviewer))

    assert get("sort=-score") == [
        "Alpha Dental",
        "Delta Orthodontics",
        "Beta_Smiles 100%",
        "Gamma Dental",  # no score: last
    ]
    assert get("sort=score")[0] == "Beta_Smiles 100%"
    assert get("sort=score")[-1] == "Gamma Dental"
    assert get("sort=name")[0] == "Alpha Dental"
    assert get("sort=-name")[0] == "Gamma Dental"
    page = client.get("/api/leads?sort=name&page=2&page_size=3", headers=as_reviewer).json()
    assert (page["total"], [lead["name"] for lead in page["items"]]) == (4, ["Gamma Dental"])


def test_lead_detail(client: TestClient, world: World, as_reviewer) -> None:
    leads = leads_world(world)
    lead = leads["alpha"]
    lead.enrichment = {"text": "very long page text", "emails": ["a@alpha.test"], "signals": {}}
    world.db.commit()

    detail = client.get(f"/api/leads/{lead.id}", headers=as_reviewer).json()
    assert detail["name"] == "Alpha Dental"
    assert detail["enrichment"] == {"emails": ["a@alpha.test"], "signals": {}}  # no page text
    assert [run["flags"] for run in detail["history"]] == [
        ["possible_duplicate:4", "category_mismatch"]
    ]
    assert detail["history"][0]["campaign_name"] == "Austin dentists"
    assert detail["history"][0]["decision"] is None
    assert detail["review"] == {
        "score_breakdown": [],
        "score_explanation": None,
        "extraction_issues": [],
    }
    assert client.get("/api/leads/999", headers=as_reviewer).status_code == 404


def test_lead_detail_after_a_decision(
    client: TestClient, world: World, worker, clock, started, as_reviewer, reviewer
) -> None:
    queue = client.get("/api/approvals/queue", headers=as_reviewer).json()
    item = queue["items"][0]
    client.post(
        f"/api/run-items/{item['run_item_id']}/decision",
        json={"decision": "approved"},
        headers=as_reviewer,
    )
    run_jobs(worker, clock)

    detail = client.get(f"/api/leads/{item['lead']['id']}", headers=as_reviewer).json()
    assert detail["status"] == "approved"
    assert detail["crm_record_id"]
    assert detail["review"]["score_breakdown"]
    assert (detail["history"][0]["decision"], detail["history"][0]["reviewer_email"]) == (
        "approved",
        reviewer.email,
    )


def test_opt_out_still_succeeds_when_a_reviewer_decided_first(
    client: TestClient, world: World, started, monkeypatch
) -> None:
    def decided_a_moment_ago(*_args, **_kwargs):
        raise NotAwaitingDecision("run item is resuming")

    monkeypatch.setattr("app.api.routes.leads.decide", decided_a_moment_ago)
    queue = client.get("/api/approvals/queue", headers=started["as_admin"]).json()
    lead_id = queue["items"][0]["lead"]["id"]

    response = client.delete(f"/api/leads/{lead_id}/data", headers=started["as_admin"])

    assert response.status_code == 204
    world.db.expire_all()
    assert world.db.get(Lead, lead_id).opt_out is True


def test_opt_out_wipes_the_lead_and_rejects_its_waiting_items(
    client: TestClient, world: World, worker, clock, started, as_reviewer
) -> None:
    queue = client.get("/api/approvals/queue", headers=as_reviewer).json()
    lead_id = queue["items"][0]["lead"]["id"]
    item_id = queue["items"][0]["run_item_id"]

    response = client.delete(f"/api/leads/{lead_id}/data", headers=started["as_admin"])
    assert response.status_code == 204

    lead = client.get(f"/api/leads/{lead_id}", headers=as_reviewer).json()
    assert lead["opt_out"] is True
    assert (lead["phone"], lead["email"], lead["website"], lead["enrichment"]) == (
        None,
        None,
        None,
        None,
    )
    # Its waiting item was rejected by the admin; the other lead still waits.
    queue = client.get("/api/approvals/queue", headers=as_reviewer).json()
    assert queue["total"] == 1
    assert queue["items"][0]["run_item_id"] != item_id
    item = client.get(f"/api/run-items/{item_id}", headers=as_reviewer).json()
    assert item["status"] == RunItemStatus.RESUMING
    assert (item["approval"]["decision"], item["approval"]["reason"]) == (
        "rejected",
        "the lead opted out",
    )
    run_jobs(worker, clock)
    assert world.crm.records == {}
    actions = world.db.scalars(select(AuditLog.action).where(AuditLog.entity_id == lead_id))
    assert "lead.opted_out" in list(actions)

    # Safe to repeat; reviewers can't do it.
    as_admin = started["as_admin"]
    assert client.delete(f"/api/leads/{lead_id}/data", headers=as_admin).status_code == 204
    assert client.delete(f"/api/leads/{lead_id}/data", headers=as_reviewer).status_code == 403
    assert client.delete("/api/leads/999/data", headers=as_admin).status_code == 404
