"""Tests for the `google_places` source. No network: HTTP is answered by `httpx2.MockTransport`.

There's no API key to record real answers with, so tests/fixtures/google_places/ holds
hand-written answers in the shape Google documents for Text Search (New).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, Lead
from app.services.ingestion import ingest
from app.sources.base import RawLead, SourceError
from app.sources.google_places import FIELD_MASK, GooglePlacesSource, expire_cached_content

FIXTURES = Path(__file__).parents[1] / "fixtures" / "google_places"
PAGES = {
    None: json.loads((FIXTURES / "text_search_page1.json").read_text()),
    "page-2-token": json.loads((FIXTURES / "text_search_page2.json").read_text()),
}


class FakeGoogle:
    """Answers Text Search from the fixtures (by pageToken) and remembers each request."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status, json={"error": {"message": "API key not valid"}})
        body = json.loads(request.content)
        return httpx2.Response(200, json=PAGES[body.get("pageToken")])

    def bodies(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


def make_source(fake: FakeGoogle) -> GooglePlacesSource:
    client = httpx2.Client(transport=httpx2.MockTransport(fake))
    return GooglePlacesSource(client, api_key="test-key")


def campaign(**options: object) -> Campaign:
    return Campaign(niche="dentist", location="Austin, TX", source_options=dict(options))


def test_collects_every_page() -> None:
    fake = FakeGoogle()
    source = make_source(fake)
    leads = list(source.collect(campaign()))

    assert [lead.name for lead in leads] == [
        "Hill Country Dental",
        "Eastside Smiles",
        "Mueller Family Dentistry",
    ]
    assert source.skipped == 1  # the place without a name

    first, second = fake.bodies()
    assert first == {"textQuery": "dentist in Austin, TX", "pageSize": 20}
    assert second == first | {"pageToken": "page-2-token"}
    assert fake.requests[0].headers["X-Goog-Api-Key"] == "test-key"
    assert fake.requests[0].headers["X-Goog-FieldMask"] == FIELD_MASK


def test_place_fields_are_mapped() -> None:
    hill = next(iter(make_source(FakeGoogle()).collect(campaign())))

    assert hill.source_key == "google_places:ChIJtest_hill_country_dental"
    assert (hill.phone, hill.website) == ("+1 512-555-0131", "https://hillcountrydental.example/")
    assert hill.address == "4100 Bee Caves Rd, Austin, TX 78746, USA"
    assert (hill.lat, hill.lng) == (30.2771, -97.7912)
    assert (hill.rating, hill.review_count) == (4.7, 318)
    assert (hill.category_hint, hill.business_status) == ("dentist", "operational")
    assert hill.raw["place"]["id"] == "ChIJtest_hill_country_dental"
    assert "fetched_at" in hill.raw


def test_max_results_stops_early() -> None:
    fake = FakeGoogle()
    leads = list(make_source(fake).collect(campaign(max_results=2)))

    assert len(leads) == 2
    assert len(fake.requests) == 1
    assert fake.bodies()[0]["pageSize"] == 2


def test_max_results_is_capped_at_googles_limit() -> None:
    fake = FakeGoogle()
    list(make_source(fake).collect(campaign(max_results=500)))
    assert fake.bodies()[0]["pageSize"] == 20


def test_bad_key_raises() -> None:
    with pytest.raises(SourceError, match="Google Places answered HTTP 403"):
        list(make_source(FakeGoogle(status=403)).collect(campaign()))


def google_lead(place_id: str, fetched_at: datetime) -> RawLead:
    return RawLead(
        source="google_places",
        source_ref=place_id,
        raw={"place": {"id": place_id}, "fetched_at": fetched_at.isoformat()},
        name=f"Dentist {place_id}",
        phone="+1 512-555-0100",
        rating=4.5,
    )


def test_expire_wipes_old_google_content_only(db: Session) -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    old = google_lead("old", now - timedelta(days=31))
    fresh = google_lead("fresh", now - timedelta(days=5))
    osm = RawLead(source="osm", source_ref="node/1", raw={}, name="OSM Dentist", phone="1")
    ingest(db, [old, fresh, osm])

    assert expire_cached_content(db, now) == 1
    # Running it again finds nothing new to expire.
    assert expire_cached_content(db, now) == 0

    leads = {lead.source_ref: lead for lead in db.scalars(select(Lead))}
    assert (leads["old"].name, leads["old"].phone, leads["old"].rating) == (
        "Dentist old",
        None,
        None,
    )
    assert leads["old"].raw == {"place_id": "old", "expired_at": now.isoformat()}
    assert leads["fresh"].phone == "+1 512-555-0100"
    assert leads["node/1"].phone == "1"
