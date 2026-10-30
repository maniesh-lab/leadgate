"""Node 3, `check`: flag possible duplicates, a wrong category and closed businesses.

Flags are short strings a reviewer sees on the lead card:
- `possible_duplicate:<lead_id>`: another lead with the same phone (E.164 or as the
  source wrote it), the same website domain, the same source ref from another source,
  or a very similar name in the same area / city (stdlib `difflib`)
- `category_mismatch`: the extracted category isn't the campaign's niche
- `closed`: the source says the business is closed

"Possible": a chain's branches share a website and a name, so a human decides.
Only leads already processed have a clean phone and city in the DB, so a duplicate is
found when the *second* copy is checked. No LLM, no side effects.
"""

from difflib import SequenceMatcher

import structlog
from langgraph.runtime import Runtime
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.agents.categories import campaign_category
from app.agents.nodes.common import load_campaign, load_lead
from app.agents.state import AgentContext, LeadState
from app.db.models import Lead

log = structlog.get_logger(__name__)

NAME_SIMILARITY = 0.9
# How many leads in the same place we compare names with (keeps big cities cheap).
MAX_NAME_CANDIDATES = 500
MAX_DUPLICATE_FLAGS = 5
CLOSED_STATUSES = {"closed", "closed_permanently", "closed_temporarily"}


def check(state: LeadState, runtime: Runtime[AgentContext]) -> LeadState:
    context = runtime.context
    lead = load_lead(context, state)
    campaign = load_campaign(context, state)
    extracted = state["extracted"]

    flags = [
        f"possible_duplicate:{lead_id}"
        for lead_id in find_duplicates(context.session, lead, state["raw"], extracted)
    ]
    category = extracted.get("category")
    matches = category == campaign_category(campaign.niche)
    if category is not None and not matches:
        flags.append("category_mismatch")
    if (state["cleaned"].get("business_status") or "") in CLOSED_STATUSES:
        flags.append("closed")

    log.info("node_check", lead_id=lead.id, flags=flags)
    return {"flags": flags, "category_matches_niche": matches}


def find_duplicates(session: Session, lead: Lead, raw: dict, extracted: dict) -> list[int]:
    """Ids of other leads that look like the same business (at most MAX_DUPLICATE_FLAGS)."""
    others = select(Lead.id).where(Lead.id != lead.id, Lead.opt_out.is_(False))

    same_contact = [and_(Lead.source_ref == lead.source_ref, Lead.source != lead.source)]
    phones = [phone for phone in (extracted.get("phone"), raw.get("phone")) if phone]
    if phones:
        same_contact.append(Lead.phone.in_(phones))
    if lead.website_domain:
        same_contact.append(Lead.website_domain == lead.website_domain)
    found = set(session.scalars(others.where(or_(*same_contact))))

    found |= similar_names(session, lead.id, extracted)
    return sorted(found)[:MAX_DUPLICATE_FLAGS]


def similar_names(session: Session, lead_id: int, extracted: dict) -> set[int]:
    """Leads in the same area (or city) whose name is almost the same."""
    place = extracted.get("area") or extracted.get("city")
    if not place:
        return set()
    rows = session.execute(
        select(Lead.id, Lead.name)
        .where(Lead.id != lead_id, Lead.opt_out.is_(False))
        .where(or_(Lead.area == place, Lead.city == place))
        .limit(MAX_NAME_CANDIDATES)
    )
    name = simple_name(extracted["name"])
    return {
        other_id
        for other_id, other_name in rows
        if SequenceMatcher(None, name, simple_name(other_name)).ratio() >= NAME_SIMILARITY
    }


def simple_name(name: str) -> str:
    """Lowercase letters and digits only: "Smile Dental, LLC" -> "smile dental llc"."""
    kept = "".join(char if char.isalnum() else " " for char in name.casefold())
    return " ".join(kept.split())
