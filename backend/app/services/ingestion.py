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
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.db.models import Campaign, Lead
from app.sources.base import LeadSource, RawLead

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
