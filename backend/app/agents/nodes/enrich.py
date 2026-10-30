"""Node 1, `enrich`: load the lead and its website enrichment into the state.

Uses `app.services.enrichment.enrich_lead()`, so a lead enriched within
ENRICH_CACHE_DAYS is not crawled again. Its only side effects are GET requests to the
business's own site and the cached enrichment on the lead row: both safe to repeat.
No LLM.
"""

import structlog
from langgraph.runtime import Runtime

from app.agents.cleaning.lead import lead_snapshot
from app.agents.nodes.common import load_lead
from app.agents.state import AgentContext, LeadState
from app.services.enrichment import enrich_lead

log = structlog.get_logger(__name__)


def enrich(state: LeadState, runtime: Runtime[AgentContext]) -> LeadState:
    context = runtime.context
    lead = load_lead(context, state)
    outcome = enrich_lead(
        context.session,
        lead,
        context.crawler,
        cache_days=context.enrich_cache_days,
        now=context.now(),
    )
    # Commit the cache now: a crash in a later node shouldn't throw the crawl away.
    context.session.commit()
    log.info("node_enrich", lead_id=lead.id, outcome=outcome)
    return {"raw": lead_snapshot(lead), "enrichment": lead.enrichment or {}}
