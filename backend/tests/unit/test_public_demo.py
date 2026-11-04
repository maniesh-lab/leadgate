"""Tests for `PUBLIC_DEMO=true`: the live demo's lockdown, whoever is logged in."""

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Campaign, Job, ScoringRubric
from app.enrichment.crawler import make_crawler_client
from app.sources.base import SourceError, make_http_client, send

MESSAGE = "this is the public demo"


@pytest.fixture
def demo_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(settings, "public_demo", True)
    return settings


def test_an_admin_cant_add_campaigns_files_or_runs(
    client: TestClient, db: Session, demo_settings, rubric: ScoringRubric, as_admin
) -> None:
    campaign = Campaign(
        name="c", niche="dentist", location="Austin", sources=["osm"], rubric_id=rubric.id
    )
    db.add(campaign)
    db.commit()
    body = {
        "name": "x",
        "niche": "dentist",
        "location": "Kathmandu",
        "sources": ["osm"],
        "rubric_id": rubric.id,
    }

    answers = [
        client.post("/api/campaigns", json=body, headers=as_admin),
        client.patch(f"/api/campaigns/{campaign.id}", json={"name": "y"}, headers=as_admin),
        client.post(f"/api/campaigns/{campaign.id}/runs", headers=as_admin),
        client.post(
            "/api/uploads/csv",
            content=b"name\nx\n",
            headers={**as_admin, "Content-Type": "text/csv"},
        ),
    ]

    for answer in answers:
        assert answer.status_code == 403, answer.text
        assert answer.json()["error"]["message"].startswith(MESSAGE)
    db.expire_all()
    assert [row.name for row in db.scalars(select(Campaign))] == ["c"]
    assert db.scalars(select(Job)).all() == []


def test_the_n8n_webhook_is_off_too(client: TestClient, demo_settings, monkeypatch) -> None:
    secret = "s" * 32
    monkeypatch.setattr(demo_settings, "n8n_webhook_secret", secret)
    answer = client.post(
        "/api/webhooks/n8n/runs",
        json={"niche": "dentist", "location": "Austin"},
        headers={"X-Webhook-Secret": secret},
    )
    assert answer.status_code == 403
    assert answer.json()["error"]["message"].startswith(MESSAGE)


def test_reading_still_works(client: TestClient, demo_settings, as_admin, as_reviewer) -> None:
    assert client.get("/api/campaigns", headers=as_reviewer).status_code == 200
    assert client.get("/api/users", headers=as_admin).status_code == 200


def test_outside_calls_never_leave_the_server() -> None:
    client = make_http_client("test-agent", blocked=True)
    with pytest.raises(SourceError, match="outside calls are off"):
        send(client, "Overpass", "POST", "https://overpass-api.de/api/interpreter")

    # No address lookup either: `resolve` would fail the test.
    def resolve(host: str) -> list[str]:
        raise AssertionError(f"looked up {host}")

    crawler = make_crawler_client("test-agent", resolve, blocked=True)
    with pytest.raises(httpx2.ConnectError, match="outside calls are off"):
        crawler.get("https://smile.example/")


def test_the_demo_only_runs_with_the_fake_llm() -> None:
    url = "sqlite://"
    with pytest.raises(ValidationError, match="PUBLIC_DEMO needs LLM_MODE=fake"):
        Settings(_env_file=None, database_url=url, public_demo=True, llm_mode="groq")
    assert Settings(_env_file=None, database_url=url, public_demo=True).public_demo
