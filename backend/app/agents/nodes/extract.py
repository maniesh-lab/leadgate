"""Node 2, `extract`: clean the lead, then let the LLM fill the fields that need reading.

Two steps:
1. Deterministic cleaning (`app.agents.cleaning.lead.clean_lead`): name, phone (E.164),
   website, address, rating, reviews and status come straight from the data. The phone
   is picked from the source's and the website's own numbers, never by the LLM.
2. The LLM (through Steadfast's validate-and-retry) fills `LLMFields`: the best email,
   area / city / country, the category, services and the founding year.

The traceability validators (`trace_validators`) make sure the LLM invents nothing:
- the email must be one of the candidate emails (the source's or the website's)
- area, city and country must appear in the source data or the campaign's place
- the category must be in CATEGORIES
- each service must appear in the website text, and the founding year right after a
  founding word there ("since 1998"; a "© 2011" copyright line doesn't count)
A failed check goes back to the model with the problem; after 3 tries the node gives
up: `extraction_ok=False`, the issues are listed, and the fields fall back to the
rule-based `baseline_fields()` (which is also the answer in LLM_MODE=fake). The graph
carries on and the router escalates the lead if it gets approved.
"""

import re

import structlog
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agents.categories import CATEGORIES, OTHER
from app.agents.cleaning.lead import (
    campaign_geo,
    clean_lead,
    find_value,
    mentions,
    short_json,
    source_text,
)
from app.agents.llm import LLM, LLMError, Validator
from app.agents.nodes.common import load_campaign
from app.agents.state import AgentContext, ExtractedLead, LeadState

log = structlog.get_logger(__name__)

MAX_SERVICES = 10
# Prompt sizes, kept small for Groq's free tier (8,000 tokens per minute).
PROMPT_TEXT_CHARS = 6000
PROMPT_SOURCE_CHARS = 3000
FIRST_YEAR = 1800
# A founding year must follow one of these words closely ("since 1998", "opened in 1991").
# A bare "© 2011" is only the copyright year: the live eval caught the LLM taking that.
FOUNDING_WORDS = re.compile(r"\b(since|established|est\.?|founded|opened|started)\b", re.I)
FOUNDING_WINDOW = 40


class LLMFields(BaseModel):
    """The part of the lead the LLM fills in. Everything else comes from cleaning."""

    email: str | None = None
    area: str | None = None
    city: str | None = None
    country: str | None = None
    category: str = OTHER
    services: list[str] = Field(default_factory=list)
    established_year: int | None = None


SYSTEM_PROMPT = (
    "You extract facts about one business for a sales lead list. Use only the data you "
    "are given. Never guess: if a fact is not in the data, use null (or [] for services). "
    "Answer with one JSON object and nothing else."
)

PROMPT_RULES = "\n".join(
    [
        "Fill in this JSON object for the business below:",
        '{"email": ..., "area": ..., "city": ..., "country": ..., "category": ...,'
        ' "services": [...], "established_year": ...}',
        "",
        "Rules:",
        "- email: the best public email for contacting the business, copied from"
        " CANDIDATE EMAILS. Prefer one on the business's own domain and a shared mailbox"
        " (info@, contact@, hello@). null if there are none.",
        "- area (neighbourhood or district), city, country: copied exactly as written in"
        " SOURCE DATA or CAMPAIGN PLACE. null if not there.",
        f"- services: up to {MAX_SERVICES} short phrases copied word for word from WEBSITE"
        ' TEXT (e.g. "teeth whitening"). [] if there is no website text.',
        "- established_year: the year the business started, only if WEBSITE TEXT says it"
        ' (e.g. "since 1998"). A copyright year is not a founding year. null otherwise.',
    ]
)


def extract(state: LeadState, runtime: Runtime[AgentContext]) -> LeadState:
    context = runtime.context
    campaign = load_campaign(context, state)
    result = extract_lead(
        context.llm,
        niche=campaign.niche,
        raw=state["raw"],
        enrichment=state.get("enrichment", {}),
        geo=campaign_geo(campaign),
        year=context.now().year,
    )
    log.info(
        "node_extract",
        lead_id=state["lead_id"],
        extraction_ok=result["extraction_ok"],
        issues=result["extraction_issues"],
    )
    return result


def extract_lead(
    llm: LLM, *, niche: str, raw: dict, enrichment: dict, geo: dict, year: int
) -> LeadState:
    """Clean + LLM-extract one lead, without the graph (also used by the extraction eval).

    Returns the state keys `cleaned`, `extracted`, `extraction_ok`, `extraction_issues`.
    """
    cleaned = clean_lead(raw, enrichment, geo["country_code"])
    website_text = enrichment.get("text") or ""
    try:
        fields = llm.structured(
            system=SYSTEM_PROMPT,
            prompt=build_prompt(niche, raw, geo, cleaned, website_text),
            schema=LLMFields,
            fake_answer=lambda: baseline_fields(cleaned, raw),
            validators=trace_validators(cleaned, source_text(raw, geo), website_text, year),
        )
        ok, issues = True, []
    except LLMError as exc:
        fields = baseline_fields(cleaned, raw)
        ok, issues = False, exc.problems

    return {
        "cleaned": cleaned,
        "extracted": merge(cleaned, fields).model_dump(),
        "extraction_ok": ok,
        "extraction_issues": issues,
    }


def merge(cleaned: dict, fields: LLMFields) -> ExtractedLead:
    """The full extracted lead: cleaned fields + the LLM's fields."""
    return ExtractedLead(
        name=cleaned["name"],
        phone=cleaned["phone"],
        email=fields.email.casefold() if fields.email else None,
        website=cleaned["website"],
        address=cleaned["address"],
        area=fields.area,
        city=fields.city,
        country=fields.country,
        category=fields.category,
        services=fields.services,
        established_year=fields.established_year,
        # No source says whether a listing is verified yet (Google Places may, later).
        verified=None,
        rating=cleaned["rating"],
        review_count=cleaned["review_count"],
    )


def baseline_fields(cleaned: dict, raw: dict) -> LLMFields:
    """The rule-based answer: the fake LLM's reply, and the fallback when the LLM fails.

    Only picks what is plainly there: the best-ranked email, the city / country the
    source stored under a city / country key, and the category the source hinted at.
    """
    data = raw.get("data")
    return LLMFields(
        email=cleaned["emails"][0] if cleaned["emails"] else None,
        city=find_value(data, ("addr:city", "city", "locality")),
        country=find_value(data, ("addr:country", "country")),
        category=cleaned["category_guess"] or OTHER,
    )


def build_prompt(niche: str, raw: dict, geo: dict, cleaned: dict, website_text: str) -> str:
    emails = ", ".join(cleaned["emails"]) or "(none)"
    text = website_text[:PROMPT_TEXT_CHARS] or "(no website text)"
    return f"""{PROMPT_RULES}
- category: exactly one of: {", ".join(CATEGORIES)}.

CAMPAIGN: looking for "{niche}" in "{geo["location"]}"
CAMPAIGN PLACE: {geo["place"] or "(unknown)"}
BUSINESS NAME: {cleaned["name"]}
ADDRESS: {cleaned["address"] or "(none)"}
SOURCE DATA ({raw.get("source")}): {short_json(raw.get("data"), PROMPT_SOURCE_CHARS)}
CANDIDATE EMAILS: {emails}
WEBSITE TEXT:
{text}"""


def trace_validators(cleaned: dict, sources: str, website_text: str, year: int) -> list[Validator]:
    """The checks that keep the LLM from inventing data (see the module docstring)."""
    candidates = set(cleaned["emails"])

    def email_is_a_candidate(fields: LLMFields) -> str | None:
        if fields.email is None or fields.email.casefold() in candidates:
            return None
        return f"email {fields.email!r} is not one of the CANDIDATE EMAILS; pick one or use null"

    def place_in_source(fields: LLMFields) -> str | None:
        missing = [
            f"{name} {value!r}"
            for name, value in (
                ("area", fields.area),
                ("city", fields.city),
                ("country", fields.country),
            )
            if value is not None and not mentions(sources, value)
        ]
        if not missing:
            return None
        return (
            f"{', '.join(missing)} not found in SOURCE DATA or CAMPAIGN PLACE; "
            "copy the exact words from there or use null"
        )

    def category_in_list(fields: LLMFields) -> str | None:
        if fields.category in CATEGORIES:
            return None
        return f"category {fields.category!r} is not in the list; use one of the listed words"

    def services_on_site(fields: LLMFields) -> str | None:
        if len(fields.services) > MAX_SERVICES:
            return f"list at most {MAX_SERVICES} services"
        invented = [service for service in fields.services if not mentions(website_text, service)]
        if not invented:
            return None
        return (
            f"services {invented} are not written in WEBSITE TEXT; "
            "copy phrases word for word or leave them out"
        )

    def year_on_site(fields: LLMFields) -> str | None:
        found = fields.established_year
        if found is None:
            return None
        if not FIRST_YEAR <= found <= year or not founding_year_in(website_text, found):
            return (
                f"established_year {found} is not given as a founding year in WEBSITE TEXT "
                '(e.g. "since 1998"; a copyright year does not count); use null'
            )
        return None

    return [email_is_a_candidate, place_in_source, category_in_list, services_on_site, year_on_site]


def founding_year_in(text: str, year: int) -> bool:
    """True when `year` appears shortly after a founding word ("since", "opened", ...)."""
    for match in re.finditer(rf"(?<!\d){year}(?!\d)", text):
        before = text[max(0, match.start() - FOUNDING_WINDOW) : match.start()]
        if FOUNDING_WORDS.search(before):
            return True
    return False
