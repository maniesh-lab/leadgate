"""Bodies for `/api/audit`: one row of the append-only audit trail."""

from datetime import datetime
from typing import Any

from app.db.models import ActorType
from app.schemas.common import FromRow


class AuditOut(FromRow):
    id: int
    occurred_at: datetime
    actor_type: ActorType
    # A user's email, a node name ("score") or "worker".
    actor: str
    # e.g. "lead.scored", "approval.approved", "run.completed"
    action: str
    entity_type: str
    entity_id: int | None
    run_id: int | None
    run_item_id: int | None
    details: dict[str, Any]
    correlation_id: str | None
