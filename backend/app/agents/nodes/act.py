"""Node 7, `act`: the only node with outside side effects (CRM write + message).

- `notify`: upsert the lead into the CRM, then post it to the leads channel
- `escalate`: post it to the admin channel only (nothing goes to the CRM until an
  admin has looked)

Safe to repeat:
- the CRM write uses the thread id as its key, so a second call updates the same
  record; and it's skipped when `crm_record_id` is already in the state
- the message is skipped when `notified` is already true. (If the process dies right
  after posting but before the checkpoint is saved, the message could go out twice;
  Phase 6 closes that gap with `run_items.notified_at`.)

A lead that opted out after it was approved gets no CRM write and no message.
Messages are plain templates (the LLM isn't needed for them).
"""

import structlog
from langgraph.runtime import Runtime

from app.agents.nodes.common import load_lead
from app.agents.nodes.route import serious_flags
from app.agents.state import AgentContext, LeadState
from app.db.models import Route
from app.services.notify import Channel

log = structlog.get_logger(__name__)


def act(state: LeadState, runtime: Runtime[AgentContext]) -> LeadState:
    context = runtime.context
    lead = load_lead(context, state)
    if lead.opt_out:
        log.info("node_act_skipped_opt_out", lead_id=lead.id)
        return {}

    update: LeadState = {}
    if state["route"] == Route.NOTIFY:
        if not state.get("crm_record_id"):
            update["crm_record_id"] = context.crm.upsert_lead(state["thread_id"], crm_fields(state))
        if not state.get("notified"):
            context.notifier.send(Channel.LEADS, lead_message(state))
            update["notified"] = True
    elif state["route"] == Route.ESCALATE and not state.get("notified"):
        context.notifier.send(Channel.ADMIN, escalation_message(state))
        update["notified"] = True

    log.info("node_act", lead_id=lead.id, route=state["route"], **update)
    return update


def crm_fields(state: LeadState) -> dict:
    """The record the CRM gets (Phase 8 maps these onto the Airtable table's fields)."""
    extracted = state["extracted"]
    return {
        "name": extracted["name"],
        "phone": extracted["phone"],
        "email": extracted["email"],
        "website": extracted["website"],
        "address": extracted["address"],
        "city": extracted["city"],
        "category": extracted["category"],
        "score": state["score"],
        "score_explanation": state["score_explanation"],
        "lead_id": state["lead_id"],
        "run_item_id": state.get("run_item_id"),
    }


def lead_message(state: LeadState) -> str:
    extracted = state["extracted"]
    contact = " · ".join(
        value for value in (extracted["phone"], extracted["email"], extracted["website"]) if value
    )
    place = f", {extracted['city']}" if extracted["city"] else ""
    return (
        f"New approved lead: {extracted['name']} ({extracted['category']}{place}), "
        f"score {state['score']}/100. {contact or 'No contact details.'}"
    )


def escalation_message(state: LeadState) -> str:
    reasons = serious_flags(state.get("flags", []))
    if not state["extraction_ok"]:
        reasons.append("extraction failed")
    return (
        f"Needs a second look: {state['extracted']['name']} (lead {state['lead_id']}) was "
        f"approved, but: {', '.join(reasons)}. Score {state['score']}/100."
    )
