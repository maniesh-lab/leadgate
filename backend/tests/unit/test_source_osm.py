"""Tests for the `osm` source. No network: HTTP is answered by `httpx2.MockTransport`.

The fixtures in tests/fixtures/osm/ are real answers recorded on 2026-10-28 with
`scripts/osm_check.py dentist "Austin, TX" --max 20 --record ...`.
Data © OpenStreetMap contributors (ODbL).
"""

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import pytest

from app.config import Settings
from app.db.models import Campaign
from app.services import ingestion
from app.sources.base import SourceError, make_http_client
from app.sources.osm import (
    OsmSource,
    build_query,
    canonical_niche,
    first_tag,
    osm_address,
    overpass_area_id,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "osm"
NOMINATIM = json.loads((FIXTURES / "nominatim.json").read_text())
OVERPASS = json.loads((FIXTURES / "overpass.json").read_text())

Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeOsm:
    """Answers Nominatim and Overpass requests from the fixtures and remembers each request."""

    def __init__(self, overpass_statuses: list[int] | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        # Status codes for the next Overpass answers (e.g. [429, 200]); 200 when used up.
        self.overpass_statuses = overpass_statuses or []
        self.nominatim_answer: list = NOMINATIM
        self.nominatim_host = "nominatim.openstreetmap.org"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.host == self.nominatim_host:
            return httpx2.Response(200, json=self.nominatim_answer)
        status = self.overpass_statuses.pop(0) if self.overpass_statuses else 200
        return httpx2.Response(status, json=OVERPASS if status == 200 else {})

    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


BACKUP = "https://overpass.backup.example/api/interpreter"


def make_source(
    handler: Handler, sleeps: list[float] | None = None, **urls: str | None
) -> OsmSource:
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    waits = sleeps if sleeps is not None else []
    return OsmSource(client, sleep=waits.append, **urls)


def unreachable(host: str, handler: Handler) -> Handler:
    """`handler`, except that `host` can't be reached (like Render's `Errno 101`)."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == host:
            raise httpx2.ConnectError("Network is unreachable", request=request)
        return handler(request)

    return answer


def campaign(**options: object) -> Campaign:
    return Campaign(niche="dentist", location="Austin, TX", source_options=dict(options))


def test_collects_every_recorded_dentist() -> None:
    leads = list(make_source(FakeOsm()).collect(campaign(max_results=20)))

    assert len(leads) == 20
    assert all(lead.source == "osm" and lead.category_hint == "dentist" for lead in leads)
    ctpd = next(lead for lead in leads if lead.source_ref == "node/2992260019")
    assert ctpd.source_key == "osm:node/2992260019"
    assert ctpd.name == "Central Texas Pediatric Dentistry"
    assert (ctpd.phone, ctpd.email) == ("+1-512-474-7575", "ctpd@ctpdkid.com")
    assert ctpd.website == "https://www.ctpdkid.com/"
    assert ctpd.address == "1201 Barbara Jordan Boulevard, Austin, TX 78723"
    assert (ctpd.lat, ctpd.lng) == (30.3053975, -97.7081725)
    assert ctpd.raw["tags"]["fax"] == "+1-512-474-7583"


def test_contact_tags_are_used_when_plain_ones_are_missing() -> None:
    leads = list(make_source(FakeOsm()).collect(campaign()))
    castle = next(lead for lead in leads if lead.source_ref == "node/3157298458")
    assert (castle.phone, castle.website) == ("+1-512-837-2900", "https://www.castledental.com/")


def test_query_searches_inside_the_city_boundary() -> None:
    fake = FakeOsm()
    list(make_source(fake).collect(campaign(max_results=20)))

    # The query is sent as a form field: data=<query>.
    query = parse_qs(fake.requests[1].content.decode())["data"][0]
    # Austin is relation 113314 -> Overpass area 3600113314.
    assert "area(id:3600113314)->.searchArea;" in query
    assert 'nwr["amenity"="dentist"]["name"](area.searchArea);' in query
    assert 'nwr["healthcare"="dentist"]["name"](area.searchArea);' in query
    assert query.endswith("out tags center 20;")


def test_geocode_is_cached_on_the_campaign() -> None:
    fake = FakeOsm()
    source = make_source(fake)
    target = campaign()

    list(source.collect(target))
    list(source.collect(target))

    assert fake.hosts().count("nominatim.openstreetmap.org") == 1
    area = target.source_options["osm_area"]
    assert (area["query"], area["osm_id"], area["country_code"]) == ("Austin, TX", 113314, "us")


def test_geocode_asks_for_english_names() -> None:
    fake = FakeOsm()
    list(make_source(fake).collect(campaign()))

    lookup = next(r for r in fake.requests if r.url.host == "nominatim.openstreetmap.org")
    assert lookup.url.params["accept-language"] == "en"


def test_new_location_is_geocoded_again() -> None:
    fake = FakeOsm()
    target = campaign()
    list(make_source(fake).collect(target))

    target.location = "Dallas, TX"
    list(make_source(fake).collect(target))

    assert fake.hosts().count("nominatim.openstreetmap.org") == 2


def test_overpass_busy_is_retried_after_a_wait() -> None:
    sleeps: list[float] = []
    leads = list(make_source(FakeOsm(overpass_statuses=[502, 429]), sleeps).collect(campaign()))

    assert len(leads) == 20
    assert sleeps == [10, 30]


def test_overpass_still_busy_after_retries_raises() -> None:
    source = make_source(FakeOsm(overpass_statuses=[429, 429, 429]))
    with pytest.raises(SourceError, match="Overpass answered HTTP 429"):
        list(source.collect(campaign()))


def test_overpass_network_error_is_retried_after_a_wait() -> None:
    fake = FakeOsm()
    calls = {"overpass": 0}

    def flaky(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "overpass-api.de":
            calls["overpass"] += 1
            if calls["overpass"] == 1:
                raise httpx2.ConnectError("timed out", request=request)
        return fake(request)

    sleeps: list[float] = []
    leads = list(make_source(flaky, sleeps).collect(campaign()))

    assert len(leads) == 20
    assert sleeps == [10]


def test_other_overpass_errors_are_not_retried() -> None:
    sleeps: list[float] = []
    fake = FakeOsm(overpass_statuses=[400])
    with pytest.raises(SourceError, match="Overpass answered HTTP 400"):
        list(make_source(fake, sleeps, overpass_backup_url=BACKUP).collect(campaign()))
    assert sleeps == []
    assert fake.hosts() == ["nominatim.openstreetmap.org", "overpass-api.de"]


def test_servers_can_be_changed() -> None:
    fake = FakeOsm()
    source = make_source(
        fake,
        nominatim_url="https://nominatim.mine.example/search",
        overpass_url="https://overpass.mine.example/api/interpreter",
    )
    fake.nominatim_host = "nominatim.mine.example"

    assert len(list(source.collect(campaign()))) == 20
    assert fake.hosts() == ["nominatim.mine.example", "overpass.mine.example"]


def test_backup_is_asked_at_once_when_overpass_is_busy() -> None:
    sleeps: list[float] = []
    fake = FakeOsm(overpass_statuses=[504])
    leads = list(make_source(fake, sleeps, overpass_backup_url=BACKUP).collect(campaign()))

    assert len(leads) == 20
    assert sleeps == []
    assert fake.hosts()[1:] == ["overpass-api.de", "overpass.backup.example"]


def test_backup_is_asked_when_overpass_cant_be_reached() -> None:
    sleeps: list[float] = []
    fake = FakeOsm()
    handler = unreachable("overpass-api.de", fake)
    leads = list(make_source(handler, sleeps, overpass_backup_url=BACKUP).collect(campaign()))

    assert len(leads) == 20
    assert sleeps == []
    assert fake.hosts()[1:] == ["overpass.backup.example"]


def test_both_busy_waits_then_asks_both_again() -> None:
    sleeps: list[float] = []
    fake = FakeOsm(overpass_statuses=[429, 502, 200])
    leads = list(make_source(fake, sleeps, overpass_backup_url=BACKUP).collect(campaign()))

    assert len(leads) == 20
    assert sleeps == [10]
    assert fake.hosts()[1:] == ["overpass-api.de", "overpass.backup.example", "overpass-api.de"]


def test_both_still_busy_after_retries_raises_the_last_error() -> None:
    sleeps: list[float] = []
    fake = FakeOsm(overpass_statuses=[429, 503] * 3)
    with pytest.raises(SourceError, match="Overpass answered HTTP 503"):
        list(make_source(fake, sleeps, overpass_backup_url=BACKUP).collect(campaign()))
    assert sleeps == [10, 30]
    assert fake.hosts().count("overpass.backup.example") == 3


def test_collect_uses_the_server_settings() -> None:
    settings = Settings(
        database_url="sqlite://",
        nominatim_url="https://nominatim.mine.example/search",
        overpass_url="https://overpass.mine.example/api/interpreter",
        overpass_backup_url=BACKUP,
    )
    client = httpx2.Client(transport=httpx2.MockTransport(FakeOsm()))
    # The OSM source needs no session.
    source = ingestion.make_source(None, "osm", campaign(), settings, client)

    assert isinstance(source, OsmSource)
    assert source.nominatim_url == "https://nominatim.mine.example/search"
    assert source.overpass_urls == ["https://overpass.mine.example/api/interpreter", BACKUP]


def test_unknown_location_raises() -> None:
    fake = FakeOsm()
    fake.nominatim_answer = []
    with pytest.raises(SourceError, match="no place called 'Austin, TX'"):
        list(make_source(fake).collect(campaign()))


def test_network_error_becomes_source_error() -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host", request=request)

    with pytest.raises(SourceError, match="Nominatim request failed"):
        list(make_source(broken).collect(campaign()))


def test_way_uses_its_center_and_unnamed_element_is_skipped() -> None:
    source = make_source(FakeOsm())
    way = {"type": "way", "id": 7, "center": {"lat": 30.1, "lon": -97.1}, "tags": {"name": "A"}}

    lead = source.element_to_lead(way, "dentist")
    assert lead is not None and (lead.lat, lead.lng, lead.source_ref) == (30.1, -97.1, "way/7")

    assert source.element_to_lead({"type": "node", "id": 8, "tags": {}}, "dentist") is None
    assert source.skipped == 1


def test_radius_searches_around_the_centre() -> None:
    area = {"osm_type": "relation", "osm_id": 1, "lat": 30.0, "lon": -97.0}
    query = build_query([("craft", "plumber")], area, radius_km=2.5, max_results=5)
    assert "(around:2500,30.0,-97.0)" in query
    assert "area(id" not in query


def test_a_point_place_falls_back_to_a_radius() -> None:
    area = {"osm_type": "node", "osm_id": 1, "lat": 27.7, "lon": 85.3}
    assert overpass_area_id(area) is None
    assert "(around:10000,27.7,85.3)" in build_query([("shop", "beauty")], area, None, 5)


@pytest.mark.parametrize(
    ("niche", "expected"),
    [("dentist", "dentist"), (" Dentists ", "dentist"), ("Realtors", "real estate agent")],
)
def test_canonical_niche(niche: str, expected: str) -> None:
    assert canonical_niche(niche) == expected


def test_unknown_niche_raises() -> None:
    with pytest.raises(SourceError, match="no OSM tags for niche 'astronaut'"):
        canonical_niche("astronaut")


def test_first_tag_keeps_one_of_several_values() -> None:
    assert first_tag({"phone": "+1 512 1; +1 512 2"}, "phone") == "+1 512 1"
    assert first_tag({}, "phone", "contact:phone") is None


def test_osm_address() -> None:
    assert osm_address({"addr:street": "Main St", "addr:state": "TX"}) == "Main St, TX"
    assert osm_address({}) is None


def test_http_client_sends_our_user_agent() -> None:
    assert make_http_client("leadgate/test").headers["User-Agent"] == "leadgate/test"
