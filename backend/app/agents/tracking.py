"""`tracked_node()`: wraps every graph node so each one leaves a `run_steps` row + an audit row.

This is the one place that logs node work (the nodes themselves don't). For each node
call on a run item it:

1. opens a `run_steps` row with status `started` and the input state, and commits it,
   so even a crash mid-node leaves a trace
2. runs the node
3. sets the row to `completed` (output, duration) or `failed` (error), adds an audit
   row (`lead.enriched`, `lead.scored`, ... or `node.failed`), and commits

Two special cases:
- `approval_gate` pausing (LangGraph's `interrupt()` raises `GraphInterrupt`) is not a
  failure: the row stays `started` while the human decides, and an `approval.requested`
  audit row is written. On resume the node runs again from its top (LangGraph's rule)
  and the wrapper reuses that open row, so the step is completed once.
- a node that died with the process (the worker was killed) also left an open row; the
  retry reuses it the same way. So every node has at most one `completed` row per item.

`duration_ms` is the time this call spent in the node. For `approval_gate`,
`finished_at - started_at` is how long the lead waited for a human.

Called by `app.agents.graph.build_graph()`. Without a `run_item_id` in the state (the
manual scripts) the node just runs, untracked.
"""

import inspect
import time
from collections.abc import Callable
from typing import Any

from langgraph.errors import GraphInterrupt
from langgraph.runtime import Runtime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.state import AgentContext, LeadState
from app.db.models import ActorType, RunItem, RunStep, StepStatus
from app.services.audit import record

# The audit action written when each node completes.
NODE_ACTIONS = {
    "enrich": "lead.enriched",
    "extract": "lead.extracted",
    "check": "lead.checked",
    "score": "lead.scored",
    "approval_gate": "lead.decided",
    "route": "lead.routed",
    "act": "lead.acted",
}
# Long values are cut in run_steps / audit rows (the website text alone is up to 20k chars).
# 500 keeps a whole score explanation (≤ 400 chars), which the API reads from the step.
MAX_TEXT = 500
MAX_ITEMS = 20

Node = Callable[..., LeadState]


def tracked_node(name: str, node: Node) -> Node:
    """`node` wrapped so every call on a run item is recorded (see the module docstring)."""
    takes_runtime = "runtime" in inspect.signature(node).parameters

    # No functools.wraps: LangGraph reads this function's own signature to see that it
    # wants `runtime`, and wraps would make it read the node's instead.
    def run(state: LeadState, runtime: Runtime[AgentContext]) -> LeadState:
        def call() -> LeadState:
            return node(state, runtime) if takes_runtime else node(state)

        if state.get("run_item_id") is None:
            return call()
        return run_tracked(name, call, state, runtime.context)

    run.__name__ = name
    return run


def run_tracked(
    name: str, call: Callable[[], LeadState], state: LeadState, context: AgentContext
) -> LeadState:
    session = context.session
    item_id = state["run_item_id"]
    step = open_step(session, item_id, name) or RunStep(
        run_item_id=item_id, node=name, status=StepStatus.STARTED, started_at=context.now()
    )
    step.input = compact(dict(state))
    session.add(step)
    session.commit()
    step_id = step.id

    started = time.perf_counter()
    try:
        update = call()
    except GraphInterrupt:
        # The approval gate paused: not an error, the row stays open until the decision.
        audit(session, state, "approval.requested", name, {"score": state.get("score")})
        session.commit()
        raise
    except Exception as exc:
        # Throw away the node's half-done changes, then record the failure on its own.
        session.rollback()
        step = session.get(RunStep, step_id)
        finish(step, context, started, StepStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        audit(session, state, "node.failed", name, {"node": name, "error": step.error})
        session.commit()
        raise

    finish(step, context, started, StepStatus.COMPLETED, output=compact(dict(update)))
    audit(session, state, NODE_ACTIONS[name], name, step.output)
    session.commit()
    return update


def open_step(session: Session, run_item_id: int, node: str) -> RunStep | None:
    """A `started` row left by a pause (approval_gate) or a crash, to be reused."""
    stmt = (
        select(RunStep)
        .where(RunStep.run_item_id == run_item_id, RunStep.node == node)
        .where(RunStep.status == StepStatus.STARTED)
        .order_by(RunStep.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def finish(
    step: RunStep,
    context: AgentContext,
    started: float,
    status: StepStatus,
    *,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    step.status = status
    step.output = output
    step.error = error
    step.finished_at = context.now()
    step.duration_ms = round((time.perf_counter() - started) * 1000)


def audit(
    session: Session, state: LeadState, action: str, node: str, details: dict[str, Any] | None
) -> None:
    item = session.get(RunItem, state["run_item_id"])
    record(
        session,
        actor_type=ActorType.AGENT,
        actor=node,
        action=action,
        entity_type="lead",
        entity_id=state["lead_id"],
        run_id=item.run_id if item else None,
        run_item_id=state["run_item_id"],
        details=details,
        correlation_id=state.get("correlation_id"),
    )


def compact(value: Any) -> Any:
    """The value with long strings and lists cut, so step and audit rows stay small."""
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return f"{value[:MAX_TEXT]}… (+{len(value) - MAX_TEXT} chars)"
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()}
    if isinstance(value, list):
        items = [compact(item) for item in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"… (+{len(value) - MAX_ITEMS} more)")
        return items
    return value
