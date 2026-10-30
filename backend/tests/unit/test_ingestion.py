"""Tests for the lead upsert in `app.services.ingestion` (SQLite)."""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Lead, LeadStatus
from app.services.ingestion import ingest, ingest_source, website_domain
from app.sources.base import RawLead
from app.sources.csv_mapped import CsvSource

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
MESSY_CSV = FIXTURES / "messy_dentists.csv"
MAPPING = json.loads((FIXTURES / "messy_dentists_mapping.json").read_text())


def raw_lead(**fields: object) -> RawLead:
    values: dict = {"source": "osm", "source_ref": "node/1", "raw": {"id": 1}, "name": "Acme"}
    return RawLead(**(values | fields))


def count_leads(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Lead)) or 0


def test_messy_csv_import(db: Session) -> None:
    result = ingest_source(db, CsvSource(MESSY_CSV, MAPPING))

    assert (result.seen, result.new, result.duplicates, result.skipped) == (7, 6, 1, 3)
    assert count_leads(db) == 6
    smile = db.scalars(select(Lead).where(Lead.name == "Smile Dental Austin")).one()
    assert smile.website_domain == "smiledental.example"
    assert smile.category == "Dentist"
    assert smile.status == LeadStatus.NEW


def test_importing_twice_adds_nothing_new(db: Session) -> None:
    ingest_source(db, CsvSource(MESSY_CSV, MAPPING))
    result = ingest_source(db, CsvSource(MESSY_CSV, MAPPING))

    assert (result.new, result.updated) == (0, 6)
    assert count_leads(db) == 6
    # Every saved lead's key is listed once (a run makes its items from these).
    assert sorted(result.saved_keys) == sorted(db.scalars(select(Lead.source_key)))


def test_recollect_refreshes_but_never_wipes(db: Session) -> None:
    ingest(db, [raw_lead(phone="512-555-0100", rating=4.0)])
    lead = db.scalars(select(Lead)).one()
    lead.status, lead.score, lead.enrichment = LeadStatus.APPROVED, 80, {"emails": ["a@b.c"]}
    db.commit()

    # The source now has a new rating and name, but no phone.
    ingest(db, [raw_lead(name="Acme Inc", rating=4.5, raw={"id": 1, "v": 2})])
    db.expire_all()
    lead = db.scalars(select(Lead)).one()

    assert (lead.name, lead.rating, lead.raw["v"]) == ("Acme Inc", 4.5, 2)
    assert lead.phone == "512-555-0100"  # kept: an empty value never wipes
    assert (lead.status, lead.score, lead.enrichment) == (
        LeadStatus.APPROVED,
        80,
        {"emails": ["a@b.c"]},
    )


def test_opted_out_lead_is_left_alone(db: Session) -> None:
    ingest(db, [raw_lead()])
    lead = db.scalars(select(Lead)).one()
    lead.opt_out, lead.phone = True, None
    db.commit()

    result = ingest(db, [raw_lead(phone="512-555-0100")])
    db.expire_all()

    assert (result.opted_out, result.updated, result.saved_keys) == (1, 0, [])
    assert db.scalars(select(Lead)).one().phone is None


@pytest.mark.parametrize(
    ("website", "domain"),
    [
        ("https://www.Example.com/contact", "example.com"),
        ("example.com", "example.com"),
        ("http://sub.example.co.uk:8080", "sub.example.co.uk"),
        (None, None),
        ("", None),
    ],
)
def test_website_domain(website: str | None, domain: str | None) -> None:
    assert website_domain(website) == domain
