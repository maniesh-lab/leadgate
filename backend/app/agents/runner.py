"""Start and resume one lead's graph, and save what it found back onto the lead row.

- `start_lead()` runs a new thread until it stops: at the approval gate (the normal
  case) or at the end. Then it saves the extracted fields, score and status on the lead.
- `resume_lead()` passes the human's decision in and runs the thread to the end.
- `waiting_for()` is the approval payload of a thread paused at the gate (None if not).

The nodes don't write the lead's fields themselves; this file does, after each run,
in one place. Phase 6's worker calls these same functions (and adds the run_items
bookkeeping). The scripts in `backend/scripts/` use them directly.
"""

from typing import Any

import structlog
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agents.nodes.approval import check_decision
from app.agents.state import AgentContext, LeadState
from app.db.models import Lead, LeadStatus, Route

log = structlog.get_logger(__name__)

ROUTE_STATUS = {
    Route.NOTIFY: LeadStatus.APPROVED,
    Route.ESCALATE: LeadStatus.ESCALATED,
    Route.SKIP: LeadStatus.REJECTED,
}
# Extracted fields copied onto the lead. An empty value never wipes a stored one.
SAVED_FIELDS = (
    "phone",
    "email",
    "area",
    "city",
    "country",
    "category",
    "services",
    "established_year",
)


def start_lead(
    graph: CompiledStateGraph,
    context: AgentContext,
    *,
    thread_id: str,
    lead_id: int,
    campaign_id: int,
    rubric_id: int,
    run_item_id: int | None = None,
    correlation_id: str | None = None,
) -> LeadState:
    """Run a new thread for the lead until the approval gate (or the end)."""
    lead = context.session.get(Lead, lead_id)
    if lead is None or lead.opt_out:
        raise ValueError(f"lead {lead_id} doesn't exist or opted out")
    first_state: LeadState = {
        "thread_id": thread_id,
        "run_item_id": run_item_id,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "rubric_id": rubric_id,
        "correlation_id": correlation_id,
    }
    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        graph.invoke(first_state, thread_config(thread_id), context=context)
    return save_result(graph, context, thread_id)


def resume_lead(
    graph: CompiledStateGraph,
    context: AgentContext,
    thread_id: str,
    *,
    decision: str,
    reason: str | None = None,
    reviewer_id: int | None = None,
) -> LeadState:
    """Give the paused thread the human's decision and run it to the end."""
    if waiting_for(graph, thread_id) is None:
        raise ValueError(f"thread {thread_id} is not waiting for a decision")
    decision, reason = check_decision(decision, reason)
    answer = {"decision": decision, "reason": reason, "reviewer_id": reviewer_id}
    correlation_id = current_state(graph, thread_id).get("correlation_id")
    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        graph.invoke(Command(resume=answer), thread_config(thread_id), context=context)
    return save_result(graph, context, thread_id)


def waiting_for(graph: CompiledStateGraph, thread_id: str) -> dict[str, Any] | None:
    """The approval payload if the thread is paused at the gate, else None."""
    interrupts = graph.get_state(thread_config(thread_id)).interrupts
    return interrupts[0].value if interrupts else None


def current_state(graph: CompiledStateGraph, thread_id: str) -> LeadState:
    return graph.get_state(thread_config(thread_id)).values


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def save_result(graph: CompiledStateGraph, context: AgentContext, thread_id: str) -> LeadState:
    """Copy the thread's results onto the lead row and commit. Returns the state."""
    state = current_state(graph, thread_id)
    lead = context.session.get(Lead, state["lead_id"])
    if lead is None:
        raise LookupError(f"lead {state['lead_id']} not found")

    extracted = state.get("extracted", {})
    for name in SAVED_FIELDS:
        if extracted.get(name) not in (None, "", []):
            setattr(lead, name, extracted[name])
    if "score" in state:
        lead.score = state["score"]
    if state.get("crm_record_id"):
        lead.crm_record_id = state["crm_record_id"]
    if "route" in state:
        lead.status = ROUTE_STATUS[Route(state["route"])]
    elif waiting_for(graph, thread_id) is not None:
        lead.status = LeadStatus.IN_REVIEW
    context.session.commit()
    log.info("lead_saved", lead_id=lead.id, status=lead.status, score=lead.score)
    return state
