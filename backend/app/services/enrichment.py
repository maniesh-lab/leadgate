"""Enrich leads: crawl each lead's own website and store what we found on the lead.

`enrich_lead()` crawls one lead's site (`app.enrichment.crawler.Crawler`), combines
the pages into one JSON dict and saves it as `lead.enrichment` + `lead.enriched_at`.
Called by `python -m app.cli enrich` (and by the agents' `enrich` node from Phase 5).

Cache: a lead enriched less than ENRICH_CACHE_DAYS ago, for the same website, is
left alone (unless forced). Opted-out leads are never enriched.

What `lead.enrichment` holds:
- `website`: the URL we started from; `final_url`: where the home page really is
- `pages`: `[{url, final_url, status, error}]` for every page we tried
- `text`: the visible text of all pages, home first, at most 20,000 characters
- `emails`: best first (on the site's own domain, then role mailboxes like info@)
- `phones`, `socials` (`{"facebook": url, ...}`)
- `signals`: see `app.enrichment.signals` (the rubric reads them as `signals.<name>`)

Nothing here changes the lead's own fields (email, phone, ...): Phase 5's extract
node decides what to keep, and may only use what's in this dict.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Lead, Run, RunItem
from app.enrichment.crawler import Crawler, FetchedPage, start_url
from app.enrichment.extract_html import (
    ParsedPage,
    find_socials,
    site_host,
    unique,
    unique_phones,
)
from app.enrichment.signals import no_site_signals, site_signals

log = structlog.get_logger(__name__)

TEXT_CAP = 20_000
MAX_EMAILS = 10
MAX_PHONES = 10
# Shared mailboxes a business reads, preferred over a person's address.
ROLE_MAILBOXES = (
    "info",
    "contact",
    "hello",
    "office",
    "reception",
    "frontdesk",
    "appointments",
    "enquiries",
    "inquiries",
)

# What `enrich_lead()` did with a lead.
ENRICHED = "enriched"
NO_WEBSITE = "no_website"
CACHED = "cached"
OPTED_OUT = "opted_out"


def enrich_lead(
    session: Session,
    lead: Lead,
    crawler: Crawler,
    *,
    cache_days: int,
    force: bool = False,
    now: datetime | None = None,
) -> str:
    """Crawl the lead's website and save the result on the lead. The caller commits.

    Returns ENRICHED, NO_WEBSITE (saved, nothing to crawl), CACHED or OPTED_OUT (both untouched).
    """
    now = now or datetime.now(UTC)
    if lead.opt_out:
        return OPTED_OUT
    if not force and is_fresh(lead, now, cache_days):
        return CACHED

    pages = crawler.crawl(lead.website) if lead.website else []
    lead.enrichment = build_enrichment(lead.website, pages, now.year)
    lead.enriched_at = now
    session.flush()

    outcome = ENRICHED if lead.website else NO_WEBSITE
    log.info(
        "lead_enriched",
        lead_id=lead.id,
        outcome=outcome,
        pages_ok=sum(page.parsed is not None for page in pages),
        pages_tried=len(pages),
        emails=len(lead.enrichment["emails"]),
    )
    return outcome


def is_fresh(lead: Lead, now: datetime, cache_days: int) -> bool:
    """True when the stored enrichment is for the same website and younger than `cache_days`."""
    if lead.enrichment is None or lead.enriched_at is None:
        return False
    if lead.enrichment.get("website") != lead.website:
        return False
    return now - lead.enriched_at < timedelta(days=cache_days)


def build_enrichment(
    website: str | None, pages: list[FetchedPage], current_year: int
) -> dict[str, Any]:
    """Combine the crawled pages into the `lead.enrichment` dict (see the module docstring)."""
    loaded = [page.parsed for page in pages if page.parsed is not None]
    home = pages[0].parsed if pages else None
    if home is not None:
        domain = site_host(home.url)
    else:
        domain = site_host(start_url(website)) if website else None
    emails = rank_emails(unique([email for page in loaded for email in page.emails]), domain)
    phones = unique_phones([phone for page in loaded for phone in page.phones])

    return {
        "website": website,
        "final_url": home.url if home else None,
        "pages": [page.summary() for page in pages],
        "text": combined_text(loaded),
        "emails": emails[:MAX_EMAILS],
        "phones": phones[:MAX_PHONES],
        "socials": find_socials([link for page in loaded for link in page.links]),
        "signals": site_signals(home, loaded, current_year) if website else no_site_signals(),
    }


def combined_text(pages: list[ParsedPage]) -> str:
    """Every page's text, home first, cut at TEXT_CAP characters."""
    return "\n\n".join(page.text for page in pages if page.text)[:TEXT_CAP]


def rank_emails(emails: list[str], site_domain: str | None) -> list[str]:
    """Best first: addresses on the site's own domain, then role mailboxes (info@, ...).

    Emails with the same rank keep the order they were found in.
    """

    def rank(email: str) -> tuple[bool, bool]:
        mailbox, domain = email.rsplit("@", 1)
        own = site_domain is not None and (
            domain == site_domain or domain.endswith(f".{site_domain}")
        )
        return (not own, mailbox not in ROLE_MAILBOXES)

    return sorted(emails, key=rank)


def campaign_leads(session: Session, campaign_id: int) -> list[Lead]:
    """The leads in any run of the campaign (run_items are the link)."""
    in_campaign = (
        select(RunItem.lead_id)
        .join(Run, Run.id == RunItem.run_id)
        .where(Run.campaign_id == campaign_id)
    )
    return list(session.scalars(select(Lead).where(Lead.id.in_(in_campaign)).order_by(Lead.id)))


def stale_leads(
    session: Session, cache_days: int, limit: int | None = None, now: datetime | None = None
) -> list[Lead]:
    """Leads never enriched, or enriched `cache_days` ago or more. Opted-out leads are left out."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=cache_days)
    stmt = (
        select(Lead)
        .where(Lead.opt_out.is_(False))
        .where(or_(Lead.enriched_at.is_(None), Lead.enriched_at <= cutoff))
        .order_by(Lead.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))
