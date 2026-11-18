"""Tests for the fake lead generator and `python -m app.cli fake-data`."""

import pytest
from faker import Faker
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.cli import main
from app.config import Settings
from app.db.fake_data import AREAS, BATCH_SIZE, CATEGORIES, fake_lead, insert_fake_leads
from app.db.fake_history import campaign_name
from app.db.models import Lead, LeadStatus


def test_inserts_the_requested_count_across_batches(db: Session) -> None:
    count = BATCH_SIZE + 5  # more than one batch
    assert insert_fake_leads(db, count, seed=1) == count
    assert db.scalar(select(func.count()).select_from(Lead)) == count


def test_leads_look_reviewed_and_consistent(db: Session) -> None:
    insert_fake_leads(db, 200, seed=2)
    leads = db.scalars(select(Lead)).all()

    assert {lead.source for lead in leads} == {"fake"}
    assert len({lead.status for lead in leads}) > 1
    for lead in leads:
        # Only reviewed leads have a score; new ones are still waiting.
        assert (lead.score is None) == (lead.status == LeadStatus.NEW)
        # Email and signals only exist when there is a website to get them from.
        if lead.website is None:
            assert lead.email is None
            assert lead.enrichment is None
        if lead.phone:
            assert lead.phone.startswith("+1")


def test_leads_can_never_be_a_real_business() -> None:
    fake = Faker("en_US")
    fake.seed_instance(4)
    words = {"dentist": ("Dental", "Dentistry", "Smiles"), "plumber": ("Plumbing", "Pipe", "Water")}
    for lead in (fake_lead(fake) for _ in range(300)):
        assert lead["category"] in CATEGORIES
        assert lead["area"] in AREAS[lead["city"]]
        if lead["category"] in words:  # the name fits the trade
            assert any(word in lead["name"] for word in words[lead["category"]])
        if lead["website_domain"]:
            assert lead["website_domain"].endswith(".example")
        if lead["phone"]:
            # Fiction's 555-01xx, above the demo's own 555-0101 to 555-0112.
            assert lead["phone"][5:9] == "5550"
            assert 150 <= int(lead["phone"][9:]) <= 199


def test_campaign_names_read_like_the_dashboard() -> None:
    assert campaign_name("dentist", "Austin") == "Dentists in Austin (sample)"


def test_same_seed_gives_same_leads() -> None:
    first, second = Faker("en_US"), Faker("en_US")
    first.seed_instance(7)
    second.seed_instance(7)
    assert fake_lead(first) == fake_lead(second)


def test_cli_fake_data(
    engine: Engine, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["fake-data", "--leads", "25"], settings) == 0
    assert "inserted 25 fake leads" in capsys.readouterr().out
