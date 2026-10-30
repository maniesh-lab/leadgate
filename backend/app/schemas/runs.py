"""Bodies for runs and run items (`/api/runs`, `/api/run-items`, `/api/campaigns/{id}/runs`)."""

from datetime import datetime
from typing import Any

from app.db.models import RunStatus, RunTrigger
from app.schemas.common import FromRow


class RunOut(FromRow):
    id: int
    campaign_id: int
    trigger: RunTrigger
    triggered_by: int | None
    rubric_id: int
    status: RunStatus
    correlation_id: str | None
    # {"collected": 20, "new": 18, ..., "items": {"done": 15, "awaiting_approval": 5}}
    stats: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
