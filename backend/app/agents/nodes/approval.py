"""Node 5, `approval_gate`: stop and wait for a human to approve or reject the lead.

`interrupt(payload)` pauses the graph; the checkpointer keeps the state, and the
payload (the lead card: key fields, flags, score + breakdown, site signals) is what
the reviewer sees. Resuming with `Command(resume={"decision": ..., "reason": ...,
"reviewer_id": ...})` makes `interrupt()` return that dict.

On resume LangGraph runs this node again from its first line, so it must have **no
side effects, ever**: it only builds the payload and checks the answer.
"""

from typing import Any

from langgraph.types import interrupt

from app.agents.state import LeadState
from app.db.models import Decision


def approval_gate(state: LeadState) -> LeadState:
    answer = interrupt(approval_request(state))
    decision, reason = check_decision(answer.get("decision"), answer.get("reason"))
    return {"decision": decision, "reason": reason, "reviewer_id": answer.get("reviewer_id")}


def check_decision(decision: str | None, reason: str | None) -> tuple[str, str | None]:
    """The decision and the trimmed reason; ValueError if they can't be accepted.

    `app.agents.runner.resume_lead()` calls this before resuming, so a bad answer never
    reaches the graph; the node checks again as a backstop.
    """
    reason = (reason or "").strip() or None
    if decision not in {Decision.APPROVED, Decision.REJECTED}:
        raise ValueError(f"decision must be 'approved' or 'rejected', not {decision!r}")
    if decision == Decision.REJECTED and reason is None:
        raise ValueError("a rejection needs a reason")
    # A plain string, so checkpoints never store our own classes.
    return Decision(decision).value, reason


def approval_request(state: LeadState) -> dict[str, Any]:
    """What the reviewer needs to decide (the lead card)."""
    extracted = state["extracted"]
    return {
        "lead_id": state["lead_id"],
        "run_item_id": state.get("run_item_id"),
        "name": extracted["name"],
        "phone": extracted["phone"],
        "email": extracted["email"],
        "website": extracted["website"],
        "city": extracted["city"],
        "category": extracted["category"],
        "flags": state.get("flags", []),
        "score": state["score"],
        "score_breakdown": state["score_breakdown"],
        "score_explanation": state["score_explanation"],
        "extraction_ok": state["extraction_ok"],
        "extraction_issues": state.get("extraction_issues", []),
        "signals": state.get("enrichment", {}).get("signals") or {},
    }
