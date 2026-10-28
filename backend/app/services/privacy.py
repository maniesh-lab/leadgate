"""Opt-out: wipe a lead's contact data and enrichment, and keep it out for good.

PLAN §4 asks for simple privacy hygiene: a business can ask us to forget it.
`opt_out_lead()` clears every contact field, the raw source data and the website
enrichment, and sets `opt_out`. The rest of the row stays (name, source_key, category,
city/area/country, rating, review state), so a later collect recognises the lead and
skips it (`app.services.ingestion`) and the crawler never visits it again
(`app.services.enrichment`).

Called by `python -m app.cli opt-out --lead <id>`; Phase 7 adds `DELETE /api/leads/{id}/data`.
"""

from sqlalchemy.orm import Session

from app.db.models import Lead

# Wiped on opt-out. The name stays as the lead's label; category/city are not contact data.
CONTACT_FIELDS = ("phone", "email", "website", "website_domain", "address", "lat", "lng")


def opt_out_lead(session: Session, lead: Lead) -> None:
    """Wipe the lead's contact data + enrichment and mark it opted out. The caller commits."""
    for field in CONTACT_FIELDS:
        setattr(lead, field, None)
    # The source's own copy holds the same contact data.
    lead.raw = {}
    lead.services = []
    lead.enrichment = None
    lead.enriched_at = None
    lead.opt_out = True
    session.flush()
