"""The audit trail: one append-only `audit_log` row per thing that happened.

Everyone writes through `record()`: the `@tracked_node` wrapper (one row per graph
node), the worker (`run.started`, `lead.collected`, `run.completed`, ...), the
approval service (`approval.approved` / `approval.rejected`) and the API routes
(`auth.login_failed`, `user.password_changed`, users, rubrics, campaigns, leads,
webhooks). Rows are only ever added; on Postgres a trigger blocks UPDATE and DELETE
(see the initial migration).

`record()` only adds the row to the session: the caller commits it together with the
change it describes, so the audit row and the change land (or fail) together.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditLog


def record(
    session: Session,
    *,
    actor_type: ActorType,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    run_id: int | None = None,
    run_item_id: int | None = None,
    details: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_type=actor_type,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        run_id=run_id,
        run_item_id=run_item_id,
        details=details or {},
        correlation_id=correlation_id,
    )
    session.add(row)
    return row
