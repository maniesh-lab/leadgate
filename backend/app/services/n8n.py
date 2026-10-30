"""The n8n results callback: when a run started by n8n finishes, post its summary back.

Switchyard's n8n starts runs through `POST /api/webhooks/n8n/runs` with a
`callback_url` (an n8n Webhook node). When the run completes or fails, the run service
queues a `send_callback` job (`app.services.runs.queue_callback`) and the worker calls
`post_callback()`, which POSTs `run_summary()` there with the same `X-Webhook-Secret`
header n8n uses to call us, so the n8n side can check it too.

A failed POST raises `CallbackError`, so the worker retries the job (3 tries). If the
worker dies right after a POST, n8n could get the same summary twice: it carries the
`run_id`, so the receiver can tell.

The summary has no contact details (phone, email, ...): approved leads are in the CRM.
"""

from typing import Any

import httpx2
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActorType, Lead, Run, RunItem
from app.services.audit import record

log = structlog.get_logger(__name__)

SECRET_HEADER = "X-Webhook-Secret"


class CallbackError(Exception):
    """n8n didn't take the summary. The worker retries the `send_callback` job."""


def post_callback(session: Session, run: Run, client: httpx2.Client, secret: str | None) -> None:
    """POST the run's summary to its `callback_url`, and audit it. Commits."""
    headers = {SECRET_HEADER: secret} if secret else {}
    try:
        response = client.post(run.callback_url, json=run_summary(session, run), headers=headers)
    except httpx2.HTTPError as exc:
        raise CallbackError(f"callback request failed: {type(exc).__name__}: {exc}") from exc
    if response.is_error:
        raise CallbackError(f"callback answered {response.status_code}: {response.text[:300]}")

    record(
        session,
        actor_type=ActorType.SYSTEM,
        actor="worker",
        action="run.callback_sent",
        entity_type="run",
        entity_id=run.id,
        run_id=run.id,
        details={"status_code": response.status_code},
        correlation_id=run.correlation_id,
    )
    session.commit()
    log.info("callback_sent", run_id=run.id, status_code=response.status_code)


def run_summary(session: Session, run: Run) -> dict[str, Any]:
    """What n8n gets: the run's status and counts, and one short line per lead."""
    rows = session.execute(
        select(RunItem, Lead)
        .join(Lead, Lead.id == RunItem.lead_id)
        .where(RunItem.run_id == run.id)
        .order_by(RunItem.id)
    )
    return {
        "run_id": run.id,
        "campaign_id": run.campaign_id,
        "status": run.status.value,
        "correlation_id": run.correlation_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "stats": run.stats,
        "leads": [
            {
                "run_item_id": item.id,
                "lead_id": lead.id,
                "name": lead.name,
                "status": item.status.value,
                # "notify" = approved and in the CRM, "escalate" = approved but needs a
                # second look, "skip" = rejected; null = it failed or opted out first.
                "route": item.route.value if item.route else None,
                "score": item.score,
                "crm_record_id": lead.crm_record_id,
                "error": item.error,
            }
            for item, lead in rows
        ],
    }
