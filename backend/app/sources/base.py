"""What every lead source shares: the `RawLead` it returns and the `LeadSource` base class.

A source turns "niche + city" (or a CSV file) into `RawLead`s. The three sources
(`csv_mapped.py`, `osm.py`, `google_places.py`) all subclass `LeadSource`, so
`app.services.ingestion` can run any of them the same way and upsert the results.

A source never crashes on one bad row: it calls `self.skip()` (log + count) and moves
on. Problems that stop the whole source (no network, bad API key) raise `SourceError`.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

import httpx2
import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Campaign

log = structlog.get_logger(__name__)

HTTP_TIMEOUT_SECONDS = 90


class SourceError(Exception):
    """The whole source failed (e.g. the API is down or the key is wrong)."""


class RawLead(BaseModel):
    """One business as a source found it, before any cleaning.

    `raw` keeps the source's own data untouched; the other fields are the common
    ones every source can fill (all optional except `name`).
    """

    # Trims every string, so " Smile Dental " and "Smile Dental" are the same.
    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    # Unique inside the source: an OSM "node/123", a Google place id, a CSV row hash.
    source_ref: str
    raw: dict[str, Any]

    name: str = Field(min_length=1)
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    # What the source says the business is ("dentist"). The extract node picks the final category.
    category_hint: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    # Lowercase, e.g. "operational", "closed_permanently".
    business_status: str | None = None

    @property
    def source_key(self) -> str:
        """The lead's unique key in the `leads` table, e.g. "osm:node/123"."""
        return f"{self.source}:{self.source_ref}"


class LeadSource(ABC):
    """Base class for a lead source. Subclasses set `name` and write `collect()`."""

    name: str

    def __init__(self) -> None:
        # How many rows the last `collect()` skipped as bad.
        self.skipped = 0

    @abstractmethod
    def collect(self, campaign: Campaign) -> Iterable[RawLead]:
        """Yield the leads this source finds for the campaign's niche + location."""

    def skip(self, reason: str, **context: Any) -> None:
        """Log one bad row and count it, instead of crashing."""
        self.skipped += 1
        log.warning("source_row_skipped", source=self.name, reason=reason, **context)


def make_http_client(user_agent: str) -> httpx2.Client:
    """The HTTP client the OSM and Google sources use (tests pass a mocked one instead)."""
    return httpx2.Client(headers={"User-Agent": user_agent}, timeout=HTTP_TIMEOUT_SECONDS)
