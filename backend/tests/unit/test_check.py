"""Tests for the duplicate rules in the `check` node (`app.agents.nodes.check`)."""

from sqlalchemy.orm import Session

from app.agents.nodes.check import find_duplicates, simple_name
from tests.agent_world import add_lead


def extracted(**fields) -> dict:
    return {"name": "Smile Austin Dental", "phone": None, "area": None, "city": None} | fields


def test_same_website_domain_is_a_possible_duplicate(db: Session) -> None:
    first = add_lead(db, phone=None)
    second = add_lead(db, phone=None)
    assert find_duplicates(db, second, {}, extracted()) == [first.id]


def test_same_phone_as_stored_or_as_written_by_the_source(db: Session) -> None:
    cleaned_one = add_lead(db, phone="+15125550142", website_domain=None)
    raw_one = add_lead(db, phone="(512) 555-0142", website_domain=None)
    lead = add_lead(db, phone="(512) 555-0142", website_domain=None)

    found = find_duplicates(db, lead, {"phone": "(512) 555-0142"}, extracted(phone="+15125550142"))

    assert found == [cleaned_one.id, raw_one.id]


def test_same_source_ref_from_another_source(db: Session) -> None:
    osm = add_lead(db, phone=None, website_domain=None)
    csv = add_lead(
        db,
        source="csv",
        source_ref=osm.source_ref,
        source_key="csv:x",
        phone=None,
        website_domain=None,
    )
    assert find_duplicates(db, csv, {}, extracted()) == [osm.id]


def test_similar_name_in_the_same_city(db: Session) -> None:
    other = add_lead(db, name="Smile Austin Dental, LLC", phone=None, website_domain=None)
    other.city = "Austin"
    elsewhere = add_lead(db, name="Smile Austin Dental", phone=None, website_domain=None)
    elsewhere.city = "Dallas"
    lead = add_lead(db, phone=None, website_domain=None)
    db.flush()

    assert find_duplicates(db, lead, {}, extracted(city="Austin")) == [other.id]
    # No place known: names alone are not compared.
    assert find_duplicates(db, lead, {}, extracted()) == []


def test_opted_out_leads_are_never_duplicates(db: Session) -> None:
    add_lead(db, phone=None, opt_out=True)
    lead = add_lead(db, phone=None)
    assert find_duplicates(db, lead, {}, extracted()) == []


def test_simple_name() -> None:
    assert simple_name("Smile  Dental, LLC.") == "smile dental llc"
