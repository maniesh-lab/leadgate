"""The public demo's data: a demo reviewer, a demo campaign and a queue that never runs dry.

`python -m app.cli demo` calls `set_up_demo()` (safe to run again and again), and the
worker calls `refill_queue()` every minute when DEMO_REVIEWER_EMAIL is set, so the queue
refills itself while the demo is awake:

- the demo reviewer (DEMO_REVIEWER_EMAIL / DEMO_REVIEWER_PASSWORD): created, or its
  password set back to the configured one. The login page shows this login to everyone,
  so the API refuses to change its password and doesn't lock it after failed logins
  (`app.api.routes.auth`).
- the demo campaign "Dentists in Austin, TX (demo)": the `csv` source on
  `demo/austin-dentists.csv` (12 made-up practices: `.example` websites, 555-01xx phones)
  and the `web-agency` rubric.
- the queue: when fewer than REFILL_BELOW of the campaign's items wait for a reviewer (or
  are on their way there), a new run of the campaign is queued; the worker takes its 12
  leads to the approval gate with the real graph (fake LLM on the demo).

The sites don't exist, so their findings are written here (`SITES`) and stored as fresh
enrichment before each run: the `enrich` node finds them cached and never crawls.
Dashboard history comes from `fake-data --history` (`--history N` on the command).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.db.models import (
    Campaign,
    Lead,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunTrigger,
    ScoringRubric,
    User,
    UserRole,
)
from app.services.ingestion import ingest_source
from app.services.runs import create_run
from app.sources.csv_mapped import CsvSource

CAMPAIGN_NAME = "Dentists in Austin, TX (demo)"
# Relative to backend/ (locally) and /app (in the image): the worker's working directory.
CSV_PATH = "demo/austin-dentists.csv"
# Start a new run when fewer items than this are waiting (or on their way).
REFILL_BELOW = 6
# Items still headed for the gate count as waiting too.
QUEUED = (
    RunItemStatus.PENDING,
    RunItemStatus.RUNNING,
    RunItemStatus.AWAITING_APPROVAL,
)
NO_SIGNALS = {
    "page_load_ok": False,
    "https": None,
    "mobile_viewport": None,
    "copyright_year": None,
    "has_form": None,
    "https_and_mobile": None,
}


def site(
    text: str,
    *,
    https: bool,
    mobile: bool,
    year: int,
    form: bool,
    emails: tuple[str, ...] = (),
) -> dict[str, Any]:
    """What a crawl of a working site would have found (the `lead.enrichment` shape)."""
    return {
        "text": text,
        "emails": list(emails),
        "signals": {
            "page_load_ok": True,
            "https": https,
            "mobile_viewport": mobile,
            "copyright_year": year,
            "has_form": form,
            "https_and_mobile": https and mobile,
        },
    }


# website (as in the CSV) -> what its pages say. None = the site doesn't load.
SITES: dict[str, dict[str, Any] | None] = {
    "http://bartoncreekdental.example": site(
        "Barton Creek Family Dental. Serving South Austin since 1998. Family dentistry, "
        "teeth cleaning, dental fillings and crowns. Call us to book a visit. © 2014",
        https=False,
        mobile=False,
        year=2014,
        form=False,
        emails=("office@bartoncreekdental.example",),
    ),
    "https://hydeparksmiles.example": site(
        "Hyde Park Smiles. Modern dentistry in the heart of Hyde Park. Teeth whitening, "
        "Invisalign, dental implants and emergency dental care. Book online in minutes. © 2026",
        https=True,
        mobile=True,
        year=2026,
        form=True,
        emails=("hello@hydeparksmiles.example",),
    ),
    "https://muellerdental.example": site(
        "Mueller Dental Studio. General dentistry, root canal treatment and teeth "
        "whitening for the whole family. Request an appointment by phone. © 2019",
        https=True,
        mobile=False,
        year=2019,
        form=False,
    ),
    "https://zilkerdental.example": site(
        "Zilker Dental Group. Cosmetic dentistry, porcelain veneers and dental implants "
        "near Zilker Park. Contact us: info@zilkerdental.example. © 2017",
        https=True,
        mobile=True,
        year=2017,
        form=True,
        emails=("info@zilkerdental.example",),
    ),
    "https://oldtowndental.example": None,
    "https://westlakehillsdentistry.example": site(
        "Westlake Hills Dentistry. Sedation dentistry, dental implants and Invisalign "
        "in Westlake. Online booking and new patient forms. © 2025",
        https=True,
        mobile=True,
        year=2025,
        form=True,
        emails=("contact@westlakehillsdentistry.example",),
    ),
    "http://crestviewkids.example": site(
        "Crestview Kids Dental. Pediatric dentistry for kids and teens since 2006. "
        "Dental sealants, fluoride treatment and first visits. © 2015",
        https=False,
        mobile=True,
        year=2015,
        form=False,
    ),
}


@dataclass
class DemoResult:
    reviewer: str  # "created" | "password reset" | "unchanged"
    campaign_id: int
    waiting: int  # items waiting (or on their way) before this call
    run_id: int | None  # the run queued now, if the queue needed one


def set_up_demo(
    session: Session, reviewer_email: str, reviewer_password: str, now: datetime
) -> DemoResult:
    """Demo reviewer + demo campaign, and a new run if the queue is running low. Commits."""
    reviewer = ensure_reviewer(session, reviewer_email, reviewer_password)
    campaign = ensure_campaign(session)
    session.commit()
    waiting = queued_items(session, campaign)
    return DemoResult(reviewer, campaign.id, waiting, refill(session, campaign, waiting, now))


def refill_queue(session: Session, now: datetime) -> int | None:
    """The worker's check: a new run when the demo campaign's queue runs low. Commits.

    Returns the new run's id, or None (no demo campaign yet, or enough items waiting).
    """
    campaign = session.scalars(select(Campaign).where(Campaign.name == CAMPAIGN_NAME)).first()
    if campaign is None:
        return None
    return refill(session, campaign, queued_items(session, campaign), now)


def refill(session: Session, campaign: Campaign, waiting: int, now: datetime) -> int | None:
    if waiting >= REFILL_BELOW or run_in_progress(session, campaign):
        return None
    prepare_leads(session, now)
    return create_run(session, campaign, now, trigger=RunTrigger.SCHEDULE).id  # commits


def ensure_reviewer(session: Session, email: str, password: str) -> str:
    email = email.strip().lower()
    user = session.scalars(select(User).where(func.lower(User.email) == email)).first()
    if user is None:
        session.add(
            User(email=email, password_hash=hash_password(password), role=UserRole.REVIEWER)
        )
        return "created"
    if user.role != UserRole.REVIEWER:
        # Its password is shown to everyone: never let that be an admin's.
        raise ValueError(f"{email} is an {user.role.value}, not a reviewer")
    if not verify_password(password, user.password_hash) or not user.is_active:
        user.password_hash = hash_password(password)
        user.is_active = True
        return "password reset"
    return "unchanged"


def ensure_campaign(session: Session) -> Campaign:
    campaign = session.scalars(select(Campaign).where(Campaign.name == CAMPAIGN_NAME)).first()
    if campaign is not None:
        return campaign
    rubric_id = session.scalar(
        select(ScoringRubric.id)
        .where(ScoringRubric.name == "web-agency")
        .order_by(ScoringRubric.version.desc())
        .limit(1)
    )
    if rubric_id is None:
        raise LookupError("no web-agency rubric: run `python -m app.cli seed` first")
    campaign = Campaign(
        name=CAMPAIGN_NAME,
        niche="dentist",
        location="Austin, TX",
        sources=["csv"],
        # country: phone numbers are read as US numbers (there's no OSM geocode here).
        source_options={"csv_path": CSV_PATH, "country": "us"},
        rubric_id=rubric_id,
    )
    session.add(campaign)
    session.flush()
    return campaign


def queued_items(session: Session, campaign: Campaign) -> int:
    return session.scalar(
        select(func.count())
        .select_from(RunItem)
        .join(Run, Run.id == RunItem.run_id)
        .where(Run.campaign_id == campaign.id, RunItem.status.in_(QUEUED))
    )


def run_in_progress(session: Session, campaign: Campaign) -> bool:
    """A run still collecting has no items yet: don't queue a second one behind it."""
    busy = (RunStatus.QUEUED, RunStatus.COLLECTING)
    return (
        session.scalar(
            select(Run.id).where(Run.campaign_id == campaign.id, Run.status.in_(busy)).limit(1)
        )
        is not None
    )


def prepare_leads(session: Session, now: datetime) -> None:
    """Save the CSV's leads now (the run's collect finds them again) with fresh enrichment."""
    csv_file = Path(CSV_PATH)
    if not csv_file.is_absolute():
        csv_file = Path(__file__).resolve().parents[2] / CSV_PATH
    result = ingest_source(session, CsvSource(csv_file))
    leads = session.scalars(select(Lead).where(Lead.source_key.in_(result.saved_keys)))
    for lead in leads:
        lead.enrichment = enrichment_for(lead.website)
        lead.enriched_at = now
    session.commit()


def enrichment_for(website: str | None) -> dict[str, Any]:
    """The stored enrichment for a demo lead: the same keys a real crawl writes."""
    common = {
        "website": website,
        "final_url": None,
        "pages": [],
        "text": "",
        "emails": [],
        "phones": [],
        "socials": {},
    }
    if website is None:
        return common | {"signals": dict.fromkeys(NO_SIGNALS)}
    found = SITES.get(website)
    if found is None:
        page = {"url": website, "final_url": None, "status": None, "error": "could not connect"}
        return common | {"pages": [page], "signals": NO_SIGNALS}
    page = {"url": website, "final_url": website + "/", "status": 200, "error": None}
    return common | {"final_url": website + "/", "pages": [page], **found}
