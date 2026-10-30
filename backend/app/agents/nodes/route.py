"""Node 6, `route`: decide what happens to the lead after the human's decision. No LLM.

- rejected → `skip` (the graph ends)
- approved, but extraction failed or a serious flag is set → `escalate` (an admin looks)
- approved and clean → `notify` (CRM + message)

Serious flags: a possible duplicate, a category mismatch, a closed business, or a
failed rubric gate. The reviewer approved anyway, so a second person should see it.
"""

from langgraph.graph import END

from app.agents.state import LeadState
from app.db.models import Decision, Route

SERIOUS_FLAGS = ("possible_duplicate", "category_mismatch", "closed", "rubric_gate")


def route(state: LeadState) -> LeadState:
    # `.value`: the state holds plain strings, so checkpoints never store our own classes.
    return {"route": pick_route(state).value}


def pick_route(state: LeadState) -> Route:
    if state["decision"] == Decision.REJECTED:
        return Route.SKIP
    if not state["extraction_ok"] or serious_flags(state.get("flags", [])):
        return Route.ESCALATE
    return Route.NOTIFY


def serious_flags(flags: list[str]) -> list[str]:
    return [flag for flag in flags if flag.split(":")[0] in SERIOUS_FLAGS]


def after_route(state: LeadState) -> str:
    """The edge after `route`: to `act`, or straight to the end for a skip."""
    return END if state["route"] == Route.SKIP else "act"
