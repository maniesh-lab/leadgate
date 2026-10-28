"""Tests for enriching leads (`app.services.enrichment`): the stored dict, the cache, opt-out.

No network: the crawler runs on `tests.fake_web`.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Campaign, Lead, Run, RunItem, RunTrigger, ScoringRubric
from app.services.enrichment import (
    CACHED,
    ENRICHED,
    NO_WEBSITE,
    OPTED_OUT,
    TEXT_CAP,
    build_enrichment,
    campaign_leads,
    enrich_lead,
    rank_emails,
    stale_leads,
)
from app.services.ingestion import ingest
from app.services.privacy import opt_out_lead
from app.sources.base import RawLead
from tests.fake_web import SMILE, SMILE_SITE, FakeWeb, make_crawler

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def add_lead(db: Session, ref: str = "node/1", website: str | None = SMILE, **fields) -> Lead:
    lead = Lead(
        source="osm",
        source_ref=ref,
        source_key=f"osm:{ref}",
        raw={"tags": {"name": "Smile Austin Dental", "phone": "+1-512-555-0142"}},
        name="Smile Austin Dental",
        website=website,
        phone="+1-512-555-0142",
        **fields,
    )
    db.add(lead)
    db.flush()
    return lead


def test_enrich_stores_everything_found_on_the_site(db: Session) -> None:
    lead = add_lead(db, website="www.smileaustin.test")

    outcome = enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=NOW)
    db.commit()

    assert outcome == ENRICHED
    db.expire_all()
    found = db.get(Lead, lead.id).enrichment
    assert db.get(Lead, lead.id).enriched_at == NOW
    assert found["website"] == "www.smileaustin.test"
    assert found["final_url"] == f"{SMILE}/"
    assert [page["error"] for page in found["pages"]] == [None, None, None, "blocked by robots.txt"]
    # Own-domain role mailboxes first, then the own-domain one, then the gmail address.
    assert found["emails"] == [
        "info@smileaustin.test",
        "frontdesk@smileaustin.test",
        "drsmith.smile@gmail.com",
    ]
    assert found["phones"] == ["+1-512-555-0142", "512.555.0143"]
    assert found["socials"] == {
        "facebook": "https://www.facebook.com/smileaustindental",
        "instagram": "https://instagram.com/smileaustin/",
        "linkedin": "https://www.linkedin.com/company/smile-austin-dental",
    }
    assert found["signals"] == {
        "page_load_ok": True,
        "https": True,
        "mobile_viewport": True,
        "copyright_year": 2019,
        "has_form": True,
        "https_and_mobile": True,
    }
    # Home page text first; the about page's services are there for Phase 5's checks.
    assert found["text"].startswith("Home About Us")
    assert "root canal therapy" in found["text"]
    # The lead's own fields are left to Phase 5's extract node.
    assert lead.email is None


def test_text_is_capped() -> None:
    web = FakeWeb({"https://big.test/": ("html", "<p>" + "word " * 10_000 + "</p>")})
    pages = make_crawler(web).crawl("https://big.test/")
    assert len(build_enrichment("https://big.test/", pages, 2026)["text"]) == TEXT_CAP


def test_a_lead_without_a_website_is_saved_without_crawling(db: Session) -> None:
    lead = add_lead(db, website=None)
    web = FakeWeb({})

    assert enrich_lead(db, lead, make_crawler(web), cache_days=14, now=NOW) == NO_WEBSITE
    assert web.requests == []
    assert lead.enriched_at == NOW
    assert lead.enrichment["pages"] == [] and lead.enrichment["emails"] == []
    assert lead.enrichment["signals"]["page_load_ok"] is None
    assert lead.enrichment["signals"]["https_and_mobile"] is None


def test_a_site_that_is_down_is_saved_as_not_loading(db: Session) -> None:
    lead = add_lead(db, website="https://down.test/")
    web = FakeWeb(
        {"https://down.test/robots.txt": ("status", "404"), "https://down.test/": ("down", "")}
    )

    assert enrich_lead(db, lead, make_crawler(web), cache_days=14, now=NOW) == ENRICHED
    assert lead.enrichment["final_url"] is None
    assert lead.enrichment["pages"][0]["error"] == "request failed: ConnectError"
    assert lead.enrichment["signals"]["page_load_ok"] is False
    assert lead.enrichment["signals"]["https"] is None


def test_a_fresh_enrichment_is_reused(db: Session) -> None:
    lead = add_lead(db)
    enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=NOW)
    web = FakeWeb(SMILE_SITE)

    later = NOW + timedelta(days=13)
    assert enrich_lead(db, lead, make_crawler(web), cache_days=14, now=later) == CACHED
    assert web.requests == []
    assert lead.enriched_at == NOW


def test_an_old_enrichment_is_redone(db: Session) -> None:
    lead = add_lead(db)
    enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=NOW)

    later = NOW + timedelta(days=14)
    assert (
        enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=later)
        == ENRICHED
    )
    assert lead.enriched_at == later


def test_force_ignores_the_cache(db: Session) -> None:
    lead = add_lead(db)
    enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=NOW)
    web = FakeWeb(SMILE_SITE)
    assert enrich_lead(db, lead, make_crawler(web), cache_days=14, force=True, now=NOW) == ENRICHED
    assert web.requests


def test_a_new_website_ignores_the_cache(db: Session) -> None:
    lead = add_lead(db, website=None)
    enrich_lead(db, lead, make_crawler(FakeWeb({})), cache_days=14, now=NOW)

    lead.website = SMILE  # a re-collect found the website
    assert (
        enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=NOW) == ENRICHED
    )
    assert lead.enrichment["website"] == SMILE


def test_opted_out_leads_are_never_crawled(db: Session) -> None:
    lead = add_lead(db, opt_out=True)
    web = FakeWeb(SMILE_SITE)
    assert enrich_lead(db, lead, make_crawler(web), cache_days=14, force=True) == OPTED_OUT
    assert web.requests == [] and lead.enrichment is None


def test_rank_emails() -> None:
    emails = ["dr.jones@gmail.com", "jane@smile.test", "info@mail.smile.test", "info@other.test"]
    assert rank_emails(emails, "smile.test") == [
        "info@mail.smile.test",  # own domain (a subdomain counts) + role mailbox
        "jane@smile.test",  # own domain
        "info@other.test",  # role mailbox elsewhere
        "dr.jones@gmail.com",
    ]
    assert rank_emails(["b@x.test", "a@y.test"], None) == ["b@x.test", "a@y.test"]


def test_campaign_leads_come_from_its_runs(db: Session) -> None:
    rubric = ScoringRubric(name="general-b2b", version=1, rules=[])
    db.add(rubric)
    db.flush()
    campaigns = [
        Campaign(name=name, niche="dentist", location="Austin, TX", rubric_id=rubric.id)
        for name in ("a", "b")
    ]
    db.add_all(campaigns)
    db.flush()
    leads = [add_lead(db, ref=f"node/{n}") for n in range(3)]
    for campaign, members in ((campaigns[0], leads[:2]), (campaigns[1], leads[1:])):
        run = Run(campaign_id=campaign.id, trigger=RunTrigger.MANUAL, rubric_id=rubric.id)
        db.add(run)
        db.flush()
        db.add_all(
            RunItem(run_id=run.id, lead_id=lead.id, thread_id=f"{run.id}-{lead.id}")
            for lead in members
        )
    # A second run with the same lead doesn't list it twice.
    rerun = Run(campaign_id=campaigns[0].id, trigger=RunTrigger.MANUAL, rubric_id=rubric.id)
    db.add(rerun)
    db.flush()
    db.add(RunItem(run_id=rerun.id, lead_id=leads[0].id, thread_id="rerun-0"))
    db.flush()

    assert campaign_leads(db, campaigns[0].id) == leads[:2]
    assert campaign_leads(db, campaigns[1].id) == leads[1:]
    assert campaign_leads(db, 999) == []


def test_stale_leads(db: Session) -> None:
    never = add_lead(db, ref="node/1")
    old = add_lead(db, ref="node/2", enriched_at=NOW - timedelta(days=20), enrichment={})
    add_lead(db, ref="node/3", enriched_at=NOW - timedelta(days=2), enrichment={})  # fresh
    add_lead(db, ref="node/4", opt_out=True)
    db.commit()

    assert stale_leads(db, cache_days=14, now=NOW) == [never, old]
    assert stale_leads(db, cache_days=14, limit=1, now=NOW) == [never]


def test_opt_out_wipes_contact_data_and_keeps_the_lead_out(db: Session) -> None:
    lead = add_lead(
        db, email="info@smileaustin.test", address="4501 S Congress Ave", lat=30.2, lng=-97.7
    )
    enrich_lead(db, lead, make_crawler(FakeWeb(SMILE_SITE)), cache_days=14, now=NOW)

    opt_out_lead(db, lead)
    db.commit()
    db.expire_all()

    wiped = db.get(Lead, lead.id)
    assert wiped.opt_out is True
    assert wiped.name == "Smile Austin Dental"  # kept as the lead's label
    for field in ("phone", "email", "website", "website_domain", "address", "lat", "lng"):
        assert getattr(wiped, field) is None
    assert (wiped.raw, wiped.services, wiped.enrichment, wiped.enriched_at) == ({}, [], None, None)

    # Collecting the same business again changes nothing.
    again = RawLead(source="osm", source_ref="node/1", raw={"tags": {}}, name="Smile", phone="1")
    assert ingest(db, [again]).opted_out == 1
    db.expire_all()
    assert db.get(Lead, lead.id).phone is None
