"""Small helpers every node uses: loading the lead / campaign rows from the DB."""

from app.agents.state import AgentContext, LeadState
from app.db.models import Campaign, Lead


def load_lead(context: AgentContext, state: LeadState) -> Lead:
    lead = context.session.get(Lead, state["lead_id"])
    if lead is None:
        raise LookupError(f"lead {state['lead_id']} not found")
    return lead


def load_campaign(context: AgentContext, state: LeadState) -> Campaign:
    campaign = context.session.get(Campaign, state["campaign_id"])
    if campaign is None:
        raise LookupError(f"campaign {state['campaign_id']} not found")
    return campaign
