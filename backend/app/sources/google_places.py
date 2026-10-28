"""The `google_places` source: businesses from the Google Places API (New).

Only runs when GOOGLE_PLACES_API_KEY is set (`app.services.ingestion` checks).
Tests use hand-written answers in the documented shape; there's no key to record with.

One Text Search request ("dentist in Austin, TX") returns up to 20 places with every
field we need, asked for with a field mask; up to 3 pages (60 places, Google's limit).
Phone, website and rating are "Text Search Enterprise" fields: 1,000 free requests a
month (checked 2026-10-28), and one request covers up to 20 places. That's cheaper than
a Place Details call per place, which bills one request per place.

Caching (Google's terms, checked 2026-10-28): a place ID may be stored forever, other
content only for a limited time. Each lead's `raw["fetched_at"]` records when Google
sent it, and `expire_cached_content()` wipes Google's content from leads not refreshed
within CACHE_DAYS (the place ID and name stay; a new collect fetches it all again).
Never scrape the Google Maps website: this official API only.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, Lead
from app.sources.base import LeadSource, RawLead, check_ok, send

log = structlog.get_logger(__name__)

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Only the fields we use: Google bills by the most expensive field asked for.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
        "places.businessStatus",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "nextPageToken",
    ]
)
DEFAULT_MAX_RESULTS = 50
PAGE_SIZE = 20  # Google's maximum per page
MAX_RESULTS_LIMIT = 60  # Text Search stops after 3 pages
CACHE_DAYS = 30
# Google content wiped by `expire_cached_content()`.
EXPIRED_COLUMNS = (
    "phone",
    "website",
    "website_domain",
    "address",
    "lat",
    "lng",
    "category",
    "rating",
    "review_count",
    "business_status",
)


class GooglePlacesSource(LeadSource):
    name = "google_places"

    def __init__(self, client: httpx2.Client, api_key: str) -> None:
        super().__init__()
        self.client = client
        self.api_key = api_key

    def collect(self, campaign: Campaign) -> Iterator[RawLead]:
        self.skipped = 0
        options = campaign.source_options or {}
        wanted = min(int(options.get("max_results", DEFAULT_MAX_RESULTS)), MAX_RESULTS_LIMIT)
        body: dict[str, Any] = {
            "textQuery": f"{campaign.niche} in {campaign.location}",
            "pageSize": min(PAGE_SIZE, wanted),
        }
        fetched_at = datetime.now(UTC).isoformat()

        places_seen = 0
        while places_seen < wanted:
            page = self.search(body)
            for place in page.get("places", [])[: wanted - places_seen]:
                places_seen += 1
                lead = self.place_to_lead(place, fetched_at)
                if lead is not None:
                    yield lead
            if not page.get("nextPageToken"):
                break
            # The next page: same query, plus the token Google gave us.
            body = {**body, "pageToken": page["nextPageToken"]}

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": FIELD_MASK}
        response = send(
            self.client, "Google Places", "POST", TEXT_SEARCH_URL, json=body, headers=headers
        )
        check_ok(response, "Google Places")
        return response.json()

    def place_to_lead(self, place: dict[str, Any], fetched_at: str) -> RawLead | None:
        location = place.get("location", {})
        status = place.get("businessStatus")
        types = place.get("types") or [None]
        try:
            return RawLead(
                source=self.name,
                source_ref=place.get("id", ""),
                raw={"place": place, "fetched_at": fetched_at},
                name=place.get("displayName", {}).get("text", ""),
                phone=place.get("internationalPhoneNumber"),
                website=place.get("websiteUri"),
                address=place.get("formattedAddress"),
                lat=location.get("latitude"),
                lng=location.get("longitude"),
                # Google lists the most specific type first, e.g. "dentist".
                category_hint=types[0],
                rating=place.get("rating"),
                review_count=place.get("userRatingCount"),
                # "OPERATIONAL" -> "operational", the form the rubrics use.
                business_status=status.lower() if status else None,
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            self.skip(f"{first['loc'][0]}: {first['msg']}", place_id=place.get("id"))
            return None


def expire_cached_content(session: Session, now: datetime | None = None) -> int:
    """Wipe Google's content from leads it sent more than CACHE_DAYS ago. Returns how many.

    Keeps the place ID (allowed forever) and the name (the lead needs a label). The
    caller commits. `ingestion.collect_campaign()` runs this before every collect.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=CACHE_DAYS)
    expired = 0
    for lead in session.scalars(select(Lead).where(Lead.source == GooglePlacesSource.name)):
        fetched_at = lead.raw.get("fetched_at")
        if fetched_at is None or datetime.fromisoformat(fetched_at) >= cutoff:
            continue
        # No "fetched_at" any more, so this lead isn't expired again.
        lead.raw = {"place_id": lead.source_ref, "expired_at": now.isoformat()}
        for column in EXPIRED_COLUMNS:
            setattr(lead, column, None)
        expired += 1
    if expired:
        session.flush()
        log.info("google_content_expired", leads=expired)
    return expired
