"""The lead graph: one LangGraph thread per lead, from website visit to CRM.

    START → enrich → extract → check → score → approval_gate (waits for a human)
          → route → act → END        (or route → END when the lead is rejected)

`build_graph(checkpointer)` takes the checkpointer as an argument: tests pass
LangGraph's `InMemorySaver`, and Phase 6 passes the Postgres one, so the same graph
survives a crash there. Nodes get their helpers (DB session, LLM, crawler, CRM,
notifier) from `AgentContext`, passed on every `invoke`. Each node is wrapped by
`app.agents.tracking.tracked_node()` (run_steps + audit rows). Use `app.agents.runner`
to start and resume a lead instead of calling the graph directly.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.nodes.act import act
from app.agents.nodes.approval import approval_gate
from app.agents.nodes.check import check
from app.agents.nodes.enrich import enrich
from app.agents.nodes.extract import extract
from app.agents.nodes.route import after_route, route
from app.agents.nodes.score import score
from app.agents.state import AgentContext, LeadState
from app.agents.tracking import tracked_node

NODES = {
    "enrich": enrich,
    "extract": extract,
    "check": check,
    "score": score,
    "approval_gate": approval_gate,
    "route": route,
    "act": act,
}


def build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    builder = StateGraph(LeadState, context_schema=AgentContext)
    # Every node is wrapped, so each call on a run item leaves a run_steps + audit row.
    for name, node in NODES.items():
        builder.add_node(name, tracked_node(name, node))

    builder.add_edge(START, "enrich")
    builder.add_edge("enrich", "extract")
    builder.add_edge("extract", "check")
    builder.add_edge("check", "score")
    builder.add_edge("score", "approval_gate")
    builder.add_edge("approval_gate", "route")
    builder.add_conditional_edges("route", after_route, ["act", END])
    builder.add_edge("act", END)
    return builder.compile(checkpointer=checkpointer)
