"""Ingestion: save the `RawLead`s a source found into the `leads` table.

Leads are upserted by `source_key` ("osm:node/123", "csv:<hash>", ...): a new key
inserts a lead, a key we already have refreshes it. So running the same source
twice adds 0 new leads. Called by `python -m app.cli ingest` / `collect`
(and by the worker's `start_run` job from Phase 6).

Upsert rules:
- a re-collect refreshes the source's own fields (name, phone, rating, ...), but an
  empty value from the source never wipes a value we already have
- review state (status, score), enrichment and the opt-out flag are never touched
- an opted-out lead is skipped entirely, so its wiped data never comes back
- the same key twice in one batch is saved once and counted as a duplicate

Postgres and SQLite both support `INSERT ... ON CONFLICT DO UPDATE`; SQLAlchemy
has a dialect-specific `insert()` for each, picked in `upsert_lead()`.

`collect_campaign()` runs every source a campaign lists (`osm`, `google_places`,
`csv`) and commits after each one, so one failing source doesn't lose the others' leads.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2
import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Campaign, Lead
from app.sources.base import LeadSource, RawLead, SourceError
from app.sources.csv_mapped import CsvSource
from app.sources.google_places import GooglePlacesSource, expire_cached_content
from app.sources.osm import OsmSource

log = structlog.get_logger(__name__)

# Columns a re-collect refreshes (an empty new value keeps the stored one).
REFRESHED_COLUMNS = (
    "name",
    "phone",
    "email",
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
DIALECT_INSERTS = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}
# How many keys go into one `WHERE source_key IN (...)` lookup.
LOOKUP_CHUNK = 500


@dataclass
class IngestResult:
    seen: int = 0  # leads the source returned
    new: int = 0  # inserted
    updated: int = 0  # already known, refreshed
    duplicates: int = 0  # same key again in this batch, saved once
    opted_out: int = 0  # known and opted out: left untouched
    skipped: int = 0  # bad rows the source skipped (set by `ingest_source`)

    def summary(self) -> str:
        return (
            f"{self.seen} found, {self.new} new, {self.updated} updated, "
            f"{self.duplicates} duplicates, {self.opted_out} opted out, "
            f"{self.skipped} bad rows skipped"
        )


@dataclass
class SourceOutcome:
    """What happened to one source in `collect_campaign()`."""

    source: str
    result: IngestResult | None = None  # set when the source ran
    skipped: str | None = None  # why it didn't run (not an error, e.g. no API key)
    error: str | None = None  # why it failed


def collect_campaign(
    session: Session, campaign: Campaign, settings: Settings, client: httpx2.Client
) -> list[SourceOutcome]:
    """Run each of the campaign's sources and upsert the leads. Commits after each source."""
    expire_cached_content(session)
    session.commit()

    outcomes = []
    for name in campaign.sources:
        outcome = SourceOutcome(source=name)
        try:
            source = make_source(name, campaign, settings, client)
            if source is None:
                outcome.skipped = "GOOGLE_PLACES_API_KEY is not set"
            else:
                outcome.result = ingest_source(session, source, campaign)
            # Also saves the OSM source's cached geocode on the campaign.
            session.commit()
        except SourceError as exc:
            session.rollback()
            outcome.error = str(exc)
            log.warning("source_failed", source=name, campaign_id=campaign.id, error=str(exc))
        outcomes.append(outcome)
    return outcomes


def make_source(
    name: str, campaign: Campaign, settings: Settings, client: httpx2.Client
) -> LeadSource | None:
    """Build the source called `name`, or None for google_places without a key."""
    if name == "osm":
        return OsmSource(client)
    if name == "google_places":
        key = settings.google_places_api_key
        return GooglePlacesSource(client, key) if key else None
    if name == "csv":
        options = campaign.source_options or {}
        if not options.get("csv_path"):
            raise SourceError("the csv source needs source_options.csv_path")
        return CsvSource(options["csv_path"], options.get("csv_mapping"))
    raise SourceError(f"unknown source {name!r}; known: osm, google_places, csv")


def ingest_source(
    session: Session, source: LeadSource, campaign: Campaign | None = None
) -> IngestResult:
    """Run one source and upsert what it finds. The caller commits.

    `campaign` may be None for the csv source (`app.cli ingest`): the file is the list.
    """
    result = ingest(session, source.collect(campaign))
    # `collect()` is a generator, so the skip count is final only after `ingest()` used it up.
    result.skipped = source.skipped
    return result


def ingest(session: Session, leads: Iterable[RawLead]) -> IngestResult:
    """Upsert the leads by `source_key`. The caller commits."""
    result = IngestResult()
    unique: dict[str, RawLead] = {}
    for lead in leads:
        result.seen += 1
        if lead.source_key in unique:
            result.duplicates += 1
        else:
            unique[lead.source_key] = lead

    known = known_leads(session, list(unique))
    for key, lead in unique.items():
        if known.get(key):  # True means opted out
            result.opted_out += 1
            continue
        upsert_lead(session, lead_values(lead))
        if key in known:
            result.updated += 1
        else:
            result.new += 1

    session.flush()
    log.info("leads_ingested", **vars(result))
    return result


def known_leads(session: Session, keys: list[str]) -> dict[str, bool]:
    """{source_key: opt_out} for the keys already in the table."""
    found: dict[str, bool] = {}
    for start in range(0, len(keys), LOOKUP_CHUNK):
        chunk = keys[start : start + LOOKUP_CHUNK]
        rows = session.execute(
            select(Lead.source_key, Lead.opt_out).where(Lead.source_key.in_(chunk))
        )
        for key, opt_out in rows:
            found[key] = opt_out
    return found


def lead_values(lead: RawLead) -> dict[str, Any]:
    """The `leads` columns for one RawLead."""
    return {
        "source": lead.source,
        "source_ref": lead.source_ref,
        "source_key": lead.source_key,
        "raw": lead.raw,
        "name": lead.name,
        "phone": lead.phone,
        "email": lead.email,
        "website": lead.website,
        "website_domain": website_domain(lead.website),
        "address": lead.address,
        "lat": lead.lat,
        "lng": lead.lng,
        # Provisional: the extract node (Phase 5) replaces it with a known category.
        "category": lead.category_hint,
        "rating": lead.rating,
        "review_count": lead.review_count,
        "business_status": lead.business_status,
    }


def upsert_lead(session: Session, values: dict[str, Any]) -> None:
    """INSERT the lead, or refresh it if its `source_key` exists (see the module docstring)."""
    insert = DIALECT_INSERTS[session.get_bind().dialect.name]
    stmt = insert(Lead).values(**values)
    refreshed = {
        column: func.coalesce(stmt.excluded[column], getattr(Lead, column))
        for column in REFRESHED_COLUMNS
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[Lead.source_key],
        set_={
            **refreshed,
            "raw": stmt.excluded.raw,
            "source_ref": stmt.excluded.source_ref,
            # ON CONFLICT skips the model's `onupdate`, so set it here.
            "updated_at": func.now(),
        },
    )
    session.execute(stmt)


def website_domain(website: str | None) -> str | None:
    """'https://www.Example.com/contact' -> 'example.com' (used to spot duplicates)."""
    if not website:
        return None
    host = urlsplit(website if "://" in website else f"http://{website}").hostname
    return host.removeprefix("www.") if host else None
