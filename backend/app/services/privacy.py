"""Opt-out: wipe a lead's contact data and enrichment, and keep it out for good.

PLAN §4 asks for simple privacy hygiene: a business can ask us to forget it.
`opt_out_lead()` clears every contact field, the raw source data and the website
enrichment, and sets `opt_out`. The rest of the row stays (name, source_key, category,
city/area/country, rating, review state), so a later collect recognises the lead and
skips it (`app.services.ingestion`) and the crawler never visits it again
(`app.services.enrichment`).

The old contact data also sits in two other places, and goes from both: the audit rows
about the lead are blanked (`redact_audit()`), and the saved LangGraph state of its
finished items is deleted (`purge_checkpoints()`). An item still waiting at the gate, or
still being worked on, keeps its state until it finishes: the worker then deletes it and
blanks the audit rows its nodes wrote in the meantime (`app.worker.handlers.finish_item`).

Called by `python -m app.cli opt-out --lead <id>` and `DELETE /api/leads/{id}/data` (which
also writes a `lead.opted_out` audit row and rejects the lead's items waiting for approval).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Lead, RunItem, RunItemStatus
from app.services.deletion import purge_checkpoints, redact_audit

# Wiped on opt-out. The name stays as the lead's label; category/city are not contact data.
CONTACT_FIELDS = ("phone", "email", "website", "website_domain", "address", "lat", "lng")
# Items whose thread is over: nothing will read their saved state again.
FINISHED = (RunItemStatus.DONE, RunItemStatus.FAILED)


def opt_out_lead(session: Session, lead: Lead) -> None:
    """Wipe the lead's contact data + enrichment (and its copies in the audit log and the
    finished items' checkpoints) and mark it opted out. The caller commits."""
    for field in CONTACT_FIELDS:
        setattr(lead, field, None)
    # The source's own copy holds the same contact data.
    lead.raw = {}
    lead.services = []
    lead.enrichment = None
    lead.enriched_at = None
    lead.opt_out = True
    session.flush()
    items = session.scalars(select(RunItem).where(RunItem.lead_id == lead.id)).all()
    purge_checkpoints(session, [item.thread_id for item in items if item.status in FINISHED])
    redact_audit(session, [lead.id], [item.id for item in items], "lead opted out")
