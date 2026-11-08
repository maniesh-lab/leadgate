"""Tests for `GET /api/exports/approved-leads` (CSV of approved leads)."""

import csv
import io

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLog, Lead
from app.services.exports import safe_cell
from tests.agent_world import World
from tests.unit.conftest import login, run_jobs

URL = "/api/exports/approved-leads"


def decide_both(client: TestClient, as_reviewer: dict) -> None:
    """Approve Smile Austin Dental, reject Barton Creek Dentistry."""
    smile, barton = client.get("/api/approvals/queue?sort=created_at", headers=as_reviewer).json()[
        "items"
    ]
    client.post(
        f"/api/run-items/{smile['run_item_id']}/decision",
        json={"decision": "approved"},
        headers=as_reviewer,
    )
    client.post(
        f"/api/run-items/{barton['run_item_id']}/decision",
        json={"decision": "rejected", "reason": "no website"},
        headers=as_reviewer,
    )


def read_csv(text: str) -> list[dict[str, str]]:
    assert text.startswith("﻿")  # Excel's UTF-8 marker
    return list(csv.DictReader(io.StringIO(text.removeprefix("﻿"))))


def test_a_runs_approved_leads_as_csv(
    client: TestClient, world: World, worker, clock, reviewer, started
) -> None:
    decide_both(client, login(client, reviewer.email))
    run_jobs(worker, clock)
    run_id = started["run"]["id"]

    response = client.get(URL, params={"run_id": run_id}, headers=started["as_admin"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="leadgate-run-{run_id}-approved.csv"'
    )
    [row] = read_csv(response.text)  # the rejected lead isn't in it
    assert row["name"] == "Smile Austin Dental"
    assert row["phone"] == "+15125550142"  # a phone keeps its "+"
    assert (row["route"], row["run_id"], row["approved_by"]) == (
        "notify",
        str(run_id),
        reviewer.email,
    )

    [audit] = world.db.scalars(select(AuditLog).where(AuditLog.action == "leads.exported")).all()
    assert (audit.entity_type, audit.entity_id, audit.run_id) == ("run", run_id, run_id)
    assert audit.details == {"rows": 1, "format": "csv"}


def test_a_campaigns_export_leaves_out_opted_out_leads(
    client: TestClient, world: World, reviewer, started
) -> None:
    decide_both(client, login(client, reviewer.email))  # approved, not yet resumed
    campaign_id = started["campaign"]["id"]
    params = {"campaign_id": campaign_id}

    [row] = read_csv(client.get(URL, params=params, headers=started["as_admin"]).text)
    assert row["route"] == ""  # the worker hasn't routed it yet

    smile = world.db.scalar(select(Lead).where(Lead.name == "Smile Austin Dental"))
    assert smile is not None
    smile.opt_out = True
    world.db.commit()
    assert read_csv(client.get(URL, params=params, headers=started["as_admin"]).text) == []


def test_export_is_admin_only_and_needs_one_target(
    client: TestClient, started, as_reviewer
) -> None:
    run_id = started["run"]["id"]
    assert client.get(URL, params={"run_id": run_id}, headers=as_reviewer).status_code == 403
    assert client.get(URL, params={"run_id": run_id}).status_code == 401
    as_admin = started["as_admin"]
    assert client.get(URL, headers=as_admin).status_code == 422
    both = {"run_id": run_id, "campaign_id": started["campaign"]["id"]}
    assert client.get(URL, params=both, headers=as_admin).status_code == 422
    assert client.get(URL, params={"run_id": 999}, headers=as_admin).status_code == 404


def test_formula_cells_are_defused() -> None:
    assert safe_cell('=HYPERLINK("http://x")') == '\'=HYPERLINK("http://x")'
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert safe_cell("+1 (512) 555-0142") == "+1 (512) 555-0142"
    assert safe_cell("Smile Dental") == "Smile Dental"
    assert safe_cell(None) == ""
    assert safe_cell(4.5) == 4.5
