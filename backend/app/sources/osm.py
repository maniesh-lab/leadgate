"""The `osm` source: businesses from OpenStreetMap. Free, no key; the default source.

Two steps:
1. Nominatim turns the campaign's location ("Austin, TX") into an OSM place. The answer
   is cached in `campaign.source_options["osm_area"]`, so each city is looked up once
   (Nominatim's policy asks clients to cache; the caller commits the campaign).
2. Overpass returns the named businesses with the niche's tags (e.g. `amenity=dentist`)
   inside that place's boundary, or within `radius_km` of its centre.

Usage policies (checked 2026-10-28): Nominatim allows at most 1 request/second and wants
a User-Agent naming the app; Overpass allows ~10,000 requests/day and answers 429/5xx
when busy, so we wait and retry twice. One collect = at most 1 Nominatim + 3 Overpass
requests. OSM data is "© OpenStreetMap contributors" (shown in the UI footer later).

OSM has no ratings, review counts or opening status, and many places lack a phone or
website; the enrichment phase fills in what it can from the business's own site.
"""

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx2
import structlog
from pydantic import ValidationError

from app.db.models import Campaign
from app.sources.base import LeadSource, RawLead, SourceError, check_ok, send

log = structlog.get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_MAX_RESULTS = 50
# Used when the place is a single point (no boundary) and no radius_km is given.
DEFAULT_RADIUS_KM = 10
OVERPASS_TIMEOUT_SECONDS = 60
# Overpass answers 429 (rate limit), 504 (no capacity) or, seen live, 502/503 when its
# queue is full and it asks for backoff: wait this long, then retry.
BUSY_STATUSES = {429, 502, 503, 504}
RETRY_WAITS_SECONDS = (10, 30)

# niche -> the OSM tags (key, value) that mean "this kind of business". Any tag matches.
NICHE_TAGS: dict[str, list[tuple[str, str]]] = {
    "dentist": [("amenity", "dentist"), ("healthcare", "dentist")],
    "doctor": [("amenity", "doctors"), ("healthcare", "doctor")],
    "pharmacy": [("amenity", "pharmacy")],
    "veterinarian": [("amenity", "veterinary")],
    "restaurant": [("amenity", "restaurant")],
    "cafe": [("amenity", "cafe")],
    "bar": [("amenity", "bar"), ("amenity", "pub")],
    "gym": [("leisure", "fitness_centre")],
    "hair salon": [("shop", "hairdresser")],
    "beauty salon": [("shop", "beauty")],
    "real estate agent": [("office", "estate_agent")],
    "lawyer": [("office", "lawyer")],
    "accountant": [("office", "accountant")],
    "insurance agent": [("office", "insurance")],
    "plumber": [("craft", "plumber")],
    "electrician": [("craft", "electrician")],
    "car repair": [("shop", "car_repair")],
    "hotel": [("tourism", "hotel")],
}
# Other words people use for the same niches.
NICHE_ALIASES = {
    "dental clinic": "dentist",
    "vet": "veterinarian",
    "fitness center": "gym",
    "hairdresser": "hair salon",
    "realtor": "real estate agent",
    "real estate": "real estate agent",
    "attorney": "lawyer",
    "mechanic": "car repair",
}


class OsmSource(LeadSource):
    name = "osm"

    def __init__(self, client: httpx2.Client, sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__()
        self.client = client
        # Tests pass a fake sleep so the retry waits take no time.
        self.sleep = sleep

    def collect(self, campaign: Campaign) -> Iterator[RawLead]:
        self.skipped = 0
        niche = canonical_niche(campaign.niche)
        options = campaign.source_options or {}
        area = self.cached_area(campaign)
        query = build_query(
            NICHE_TAGS[niche],
            area,
            options.get("radius_km"),
            int(options.get("max_results", DEFAULT_MAX_RESULTS)),
        )
        for element in self.overpass(query).get("elements", []):
            lead = self.element_to_lead(element, niche)
            if lead is not None:
                yield lead

    def cached_area(self, campaign: Campaign) -> dict[str, Any]:
        """The campaign's place from Nominatim, looked up once and kept in source_options."""
        options = campaign.source_options or {}
        cached = options.get("osm_area")
        if cached and cached.get("query") == campaign.location:
            return cached
        area = self.geocode(campaign.location)
        # A new dict (not an in-place change), so SQLAlchemy notices and saves it.
        campaign.source_options = {**options, "osm_area": area}
        return area

    def geocode(self, location: str) -> dict[str, Any]:
        params = {"q": location, "format": "jsonv2", "limit": 1, "addressdetails": 1}
        response = send(self.client, "Nominatim", "GET", NOMINATIM_URL, params=params)
        check_ok(response, "Nominatim")
        results = response.json()
        if not results:
            raise SourceError(f"Nominatim found no place called {location!r}")
        place = results[0]
        return {
            "query": location,
            "osm_type": place["osm_type"],
            "osm_id": place["osm_id"],
            "lat": float(place["lat"]),
            "lon": float(place["lon"]),
            "display_name": place.get("display_name"),
            # e.g. "us": Phase 5 uses it to normalize phone numbers.
            "country_code": place.get("address", {}).get("country_code"),
        }

    def overpass(self, query: str) -> dict[str, Any]:
        response = send(self.client, "Overpass", "POST", OVERPASS_URL, data={"data": query})
        for wait in RETRY_WAITS_SECONDS:
            if response.status_code not in BUSY_STATUSES:
                break
            log.warning("overpass_busy", status=response.status_code, retry_in_seconds=wait)
            self.sleep(wait)
            response = send(self.client, "Overpass", "POST", OVERPASS_URL, data={"data": query})
        check_ok(response, "Overpass")
        return response.json()

    def element_to_lead(self, element: dict[str, Any], niche: str) -> RawLead | None:
        tags = element.get("tags", {})
        ref = f"{element.get('type')}/{element.get('id')}"
        # Nodes have lat/lon; ways and relations have a "center" (we ask for `out center`).
        point = element if "lat" in element else element.get("center", {})
        try:
            return RawLead(
                source=self.name,
                source_ref=ref,
                raw=element,
                name=tags.get("name", ""),
                phone=first_tag(tags, "phone", "contact:phone"),
                email=first_tag(tags, "email", "contact:email"),
                website=first_tag(tags, "website", "contact:website", "url"),
                address=osm_address(tags),
                lat=point.get("lat"),
                lng=point.get("lon"),
                # Overpass only returned it because it has the niche's tags.
                category_hint=niche,
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            self.skip(f"{first['loc'][0]}: {first['msg']}", osm=ref)
            return None


def canonical_niche(niche: str) -> str:
    """'Dentists ' -> 'dentist'. Raises SourceError for a niche we have no OSM tags for."""
    name = " ".join(niche.lower().split())
    name = NICHE_ALIASES.get(name, name)
    if name not in NICHE_TAGS and name.endswith("s"):
        name = NICHE_ALIASES.get(name[:-1], name[:-1])
    if name not in NICHE_TAGS:
        raise SourceError(f"no OSM tags for niche {niche!r}; known: {', '.join(NICHE_TAGS)}")
    return name


def build_query(
    tags: list[tuple[str, str]], area: dict[str, Any], radius_km: float | None, max_results: int
) -> str:
    """The Overpass QL query: named places with any of the tags, inside the area."""
    area_filter, area_setup = search_filter(area, radius_km)
    lines = [f'  nwr["{key}"="{value}"]["name"]{area_filter};' for key, value in tags]
    return "\n".join(
        [
            f"[out:json][timeout:{OVERPASS_TIMEOUT_SECONDS}];",
            *area_setup,
            "(",
            *lines,
            ");",
            # tags + a centre point for ways/relations, at most max_results elements.
            f"out tags center {max_results};",
        ]
    )


def search_filter(area: dict[str, Any], radius_km: float | None) -> tuple[str, list[str]]:
    """Search inside the place's boundary, or (no boundary / radius given) around its centre."""
    area_id = overpass_area_id(area)
    if radius_km is None and area_id is not None:
        return "(area.searchArea)", [f"area(id:{area_id})->.searchArea;"]
    meters = int(float(radius_km or DEFAULT_RADIUS_KM) * 1000)
    return f"(around:{meters},{area['lat']},{area['lon']})", []


def overpass_area_id(area: dict[str, Any]) -> int | None:
    # Overpass numbers areas as 3600000000 + relation id, or 2400000000 + way id.
    if area["osm_type"] == "relation":
        return 3_600_000_000 + int(area["osm_id"])
    if area["osm_type"] == "way":
        return 2_400_000_000 + int(area["osm_id"])
    return None


def first_tag(tags: dict[str, str], *keys: str) -> str | None:
    """The first of these tags that is set. OSM separates several values with ';': keep one."""
    for key in keys:
        if tags.get(key):
            return tags[key].split(";")[0].strip()
    return None


def osm_address(tags: dict[str, str]) -> str | None:
    """'1200 Congress Ave, Austin, TX 78701' from the addr:* tags (None if there are none)."""
    street = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))
    region = " ".join(filter(None, [tags.get("addr:state"), tags.get("addr:postcode")]))
    parts = [street, tags.get("addr:city"), region]
    return ", ".join(part for part in parts if part) or None
