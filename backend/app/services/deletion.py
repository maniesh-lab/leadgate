"""Delete leads, runs and campaigns, and blank what the audit log knew about the leads.

Boss's rule (2026-11-08): the audit rows stay (who did what, and when), but a deleted
lead's name, contact data and source data are blanked in them. `redact_audit()` keeps
only the `SAFE_DETAILS` keys of each row's `details` and adds `"redacted": <why>`. On
Postgres the append-only trigger allows exactly that (see the `audit_redaction`
migration).

What each delete removes:
- `delete_lead()`: the lead, its run items, their steps, approvals, jobs and LangGraph
  checkpoints, then blanks the audit rows about it. Not an opted-out lead (`OptedOut`):
  its wiped row is what keeps it out. Its runs' statuses are refreshed
  (a run waiting only on this lead is completed).
- `delete_runs()`: the runs, their jobs and all their items (as above), and every lead
  left without a run item in another run (blanked in the audit log too), apart from an
  opted-out one: its wiped row stays, so a later collect still skips it.
- `delete_campaign()`: all its runs (as above), then the campaign, and its uploaded CSV
  when no other campaign uses it.
- `delete_upload()`: an uploaded CSV no campaign uses (`InUse` otherwise).

Each refuses (`StillWorking`) while the worker may still touch what it would delete: a
queued or running job for one of the runs or items, a run still collecting, or an item
that is pending, running or resuming. Items waiting for a decision may go, and so may a
queued run whose job is gone.

Nothing outside leadgate changes: a lead already sent to the CRM or Discord stays there.
A deleted lead can be found again by a later run; an opt-out (`app.services.privacy`) is
what keeps a business out for good.

Also used by the opt-out: `redact_audit()`, `redact_steps()` and `purge_checkpoints()`.

The caller writes its own audit row (`lead.deleted`, ...) and commits.
"""

from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, exists, or_, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    Approval,
    AuditLog,
    Campaign,
    CsvUpload,
    Job,
    JobStatus,
    Lead,
    Run,
    RunItem,
    RunStatus,
    RunStep,
)
from app.services.runs import BUSY, refresh_run_status

# The `details` keys a blanked audit row keeps: ids, numbers, flags and decisions, never
# a name, contact detail, source data, website text, free-text reason or error message.
SAFE_DETAILS = frozenset(
    {
        "category_checked",
        "category_matches_niche",
        "crm_record_id",
        "decision",
        "extraction_ok",
        "flags",
        "job",
        "lead_id",
        "node",
        "notified",
        "reviewer_id",
        "route",
        "score",
        "score_breakdown",
    }
)

# LangGraph's own tables (made by `PostgresSaver.setup()`); each has a `thread_id`.
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
ACTIVE_JOBS = (JobStatus.QUEUED, JobStatus.RUNNING)


class StillWorking(Exception):
    """The worker may still touch something the delete would remove. Try again later."""


class InUse(Exception):
    """A campaign still uses the uploaded CSV."""


class OptedOut(Exception):
    """The lead opted out: its wiped row is what keeps it from being collected again."""


@dataclass
class Deleted:
    """How much a delete removed: the details of its audit row."""

    campaigns: int = 0
    runs: int = 0
    run_items: int = 0
    leads: int = 0
    csv_uploads: int = 0

    def details(self) -> dict[str, int]:
        return asdict(self)


def delete_lead(session: Session, lead: Lead, now: datetime) -> Deleted:
    if lead.opt_out:
        raise OptedOut("this lead opted out: its row (already wiped) keeps it out for good")
    lead_id = lead.id
    items = session.scalars(fresh(select(RunItem).where(RunItem.lead_id == lead_id))).all()
    check_idle(session, items)
    run_ids = {item.run_id for item in items}
    item_ids = [item.id for item in items]
    remove_items(session, items)
    redact_audit(session, [lead_id], item_ids, "lead deleted")
    session.execute(delete(Lead).where(Lead.id == lead_id))
    for run in session.scalars(select(Run).where(Run.id.in_(run_ids))):
        refresh_run_status(session, run, now)
    return Deleted(run_items=len(items), leads=1)


def delete_runs(session: Session, runs: Sequence[Run]) -> Deleted:
    run_ids = [run.id for run in runs]
    if busy := [run.id for run in runs if run.status == RunStatus.COLLECTING]:
        raise StillWorking(f"run {busy[0]} is still collecting leads")
    if job_ids(session, Job.payload["run_id"].as_integer().in_(run_ids), ACTIVE_JOBS):
        raise StillWorking("the worker still has a job queued for this run")
    items = session.scalars(fresh(select(RunItem).where(RunItem.run_id.in_(run_ids)))).all()
    check_idle(session, items)

    lead_of = {item.id: item.lead_id for item in items}  # read before the rows go
    remove_items(session, items)
    # Leads that were only in these runs go too; a lead also in another run stays. So does
    # an opted-out lead: its row (already wiped) is what keeps it from being collected again.
    orphans = set(
        session.scalars(
            select(Lead.id).where(
                Lead.id.in_(set(lead_of.values())),
                ~exists().where(RunItem.lead_id == Lead.id),
                Lead.opt_out.is_(False),
            )
        )
    )
    orphan_items = [item_id for item_id, lead_id in lead_of.items() if lead_id in orphans]
    redact_audit(session, sorted(orphans), orphan_items, "lead deleted")
    session.execute(delete(Lead).where(Lead.id.in_(orphans)))
    session.execute(delete(Job).where(Job.payload["run_id"].as_integer().in_(run_ids)))
    session.execute(delete(Run).where(Run.id.in_(run_ids)))
    return Deleted(runs=len(run_ids), run_items=len(lead_of), leads=len(orphans))


def delete_campaign(session: Session, campaign: Campaign) -> Deleted:
    campaign_id = campaign.id
    upload_id = (campaign.source_options or {}).get("csv_upload_id")
    runs = session.scalars(fresh(select(Run).where(Run.campaign_id == campaign_id))).all()
    deleted = delete_runs(session, runs)
    session.execute(delete(Campaign).where(Campaign.id == campaign_id))
    deleted.campaigns = 1
    if upload_id is not None and not campaigns_using(session, upload_id):
        deleted.csv_uploads = session.execute(
            delete(CsvUpload).where(CsvUpload.id == upload_id)
        ).rowcount
    return deleted


def delete_upload(session: Session, upload: CsvUpload) -> Deleted:
    if using := campaigns_using(session, upload.id):
        names = ", ".join(str(campaign_id) for campaign_id in using)
        raise InUse(f"this file is still used by campaign {names}: delete that campaign first")
    session.delete(upload)
    session.flush()
    return Deleted(csv_uploads=1)


def campaigns_using(session: Session, upload_id: int) -> list[int]:
    """The campaigns (archived ones too) that read this uploaded CSV."""
    uses = Campaign.source_options["csv_upload_id"].as_integer() == upload_id
    return list(session.scalars(select(Campaign.id).where(uses).order_by(Campaign.id)))


def fresh[T: Select](stmt: T) -> T:
    """The query, set to reload rows the session already holds: the statuses that decide
    whether a delete may go ahead must be the database's, not an old copy's."""
    return stmt.execution_options(populate_existing=True)


def check_idle(session: Session, items: Sequence[RunItem]) -> None:
    """StillWorking if the worker may still be on one of these items."""
    if busy := [item for item in items if item.status in BUSY]:
        raise StillWorking(f"run item {busy[0].id} is still {busy[0].status.value}")
    item_ids = [item.id for item in items]
    if item_ids and job_ids(
        session, Job.payload["run_item_id"].as_integer().in_(item_ids), ACTIVE_JOBS
    ):
        raise StillWorking("the worker still has a job queued for one of these leads")


def job_ids(
    session: Session, where: ColumnElement[bool], statuses: Collection[JobStatus]
) -> list[int]:
    return list(session.scalars(select(Job.id).where(where, Job.status.in_(statuses))))


def remove_items(session: Session, items: Sequence[RunItem]) -> None:
    """Delete the items with their steps, approvals, jobs and checkpoints."""
    if not items:
        return
    ids = [item.id for item in items]
    purge_checkpoints(session, [item.thread_id for item in items])
    session.execute(delete(RunStep).where(RunStep.run_item_id.in_(ids)))
    session.execute(delete(Approval).where(Approval.run_item_id.in_(ids)))
    session.execute(delete(Job).where(Job.payload["run_item_id"].as_integer().in_(ids)))
    session.execute(delete(RunItem).where(RunItem.id.in_(ids)))
    session.expire_all()  # the ORM still holds the deleted rows


def purge_checkpoints(session: Session, thread_ids: list[str]) -> None:
    """Delete these threads' saved LangGraph state. Postgres only: elsewhere the
    checkpointer keeps it in memory (`app.agents.checkpoint`)."""
    if not thread_ids or session.get_bind().dialect.name != "postgresql":
        return
    for table in CHECKPOINT_TABLES:
        # The tables only exist once a worker has started (`PostgresSaver.setup()`).
        if session.scalar(text("SELECT to_regclass(:name)"), {"name": table}) is None:
            continue
        # `table` is one of the fixed names above, never input.
        session.execute(
            text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"), {"ids": thread_ids}
        )


def redact_audit(
    session: Session, lead_ids: Sequence[int], item_ids: Sequence[int], why: str
) -> int:
    """Blank the audit rows about these leads and run items: keep only `SAFE_DETAILS`.

    Returns how many rows changed.
    """
    about = [and_(AuditLog.entity_type == "lead", AuditLog.entity_id.in_(lead_ids))]
    if item_ids:
        about.append(AuditLog.run_item_id.in_(item_ids))
        about.append(and_(AuditLog.entity_type == "run_item", AuditLog.entity_id.in_(item_ids)))
    if session.get_bind().dialect.name == "postgresql":
        # Lets the trigger accept a details-only UPDATE until this transaction ends.
        session.execute(text("SELECT set_config('leadgate.audit_redaction', 'on', true)"))
    changed = 0
    for row in session.scalars(select(AuditLog).where(or_(*about))):
        blanked = blank(row.details, why)
        if blanked != row.details:
            row.details = blanked
            changed += 1
    session.flush()
    return changed


def redact_steps(session: Session, item_ids: Sequence[int], why: str) -> int:
    """Blank these run items' steps: their input and output keep only `SAFE_DETAILS` (a
    step stores the whole state: source data, website text, contact fields), and an error
    message goes. Returns how many rows changed."""
    if not item_ids:
        return 0
    changed = 0
    for step in session.scalars(select(RunStep).where(RunStep.run_item_id.in_(item_ids))):
        blanked = (blank(step.input, why), blank(step.output, why), why if step.error else None)
        if blanked != (step.input, step.output, step.error):
            step.input, step.output, step.error = blanked
            changed += 1
    session.flush()
    return changed


def blank(details: dict[str, Any] | None, why: str) -> dict[str, Any] | None:
    """`details` with only the `SAFE_DETAILS` keys, plus `"redacted": <why>`."""
    if details is None:
        return None
    kept = {key: value for key, value in details.items() if key in SAFE_DETAILS}
    kept["redacted"] = why
    return kept
