"""SQLAlchemy models: one class per table from PLAN §6.

Everything the app stores lives here: users, scoring rubrics, campaigns, leads,
runs and their per-lead items and steps, human approvals, the append-only audit
log and the job queue the worker reads. The Alembic migration creates these
tables; unit tests create them straight from `Base.metadata` on SQLite.

Status-like columns use the `StrEnum`s below and are stored as plain strings
with a CHECK constraint (see `str_enum()` in `base.py`).

Indexes: each one is backed by an EXPLAIN ANALYZE plan of the query it serves (the
API's lists, the worker's claim, the check node's duplicate search, the reports); see
`docs/sql-notes.md` and `scripts/sql_explain.py`. Indexes that were measured and didn't
help are listed there too.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, UTCDateTime, str_enum

# --- Enums (the allowed values of each status-like column) ---


class UserRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class LeadStatus(StrEnum):
    NEW = "new"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class RunTrigger(StrEnum):
    MANUAL = "manual"
    N8N = "n8n"
    SCHEDULE = "schedule"


class RunStatus(StrEnum):
    QUEUED = "queued"
    COLLECTING = "collecting"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class RunItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RESUMING = "resuming"
    DONE = "done"
    FAILED = "failed"


class Route(StrEnum):
    NOTIFY = "notify"
    SKIP = "skip"
    ESCALATE = "escalate"


class StepStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ActorType(StrEnum):
    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"


class JobType(StrEnum):
    START_RUN = "start_run"
    PROCESS_ITEM = "process_item"
    RESUME_ITEM = "resume_item"
    SEND_CALLBACK = "send_callback"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# --- Tables ---


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole, "role"))
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class ScoringRubric(Base):
    """A versioned list of scoring rules. Editing a rubric adds a new version."""

    __tablename__ = "scoring_rubrics"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    version: Mapped[int]
    # [{"label", "field", "op", "value", "points", "required"?}, ...]
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    niche: Mapped[str]
    location: Mapped[str]
    # e.g. ["osm", "csv"]
    sources: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # e.g. {"csv_path": ..., "csv_mapping": {...}, "radius_km": 10, "max_results": 50}
    source_options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    rubric_id: Mapped[int] = mapped_column(ForeignKey("scoring_rubrics.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    is_archived: Mapped[bool] = mapped_column(server_default=false())


class Lead(Base):
    """One business. The same business found again (same `source_key`) updates this row."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_range"),
        CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="rating_range"),
        # `?q=` (name ILIKE '%...%'): a trigram index can serve a search inside the name.
        # On SQLite (unit tests) this is a plain index; the pg_trgm part is Postgres-only.
        Index(
            "ix_leads_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]
    # source_ref, phone and website_domain are indexed for the check node's duplicate search.
    source_ref: Mapped[str] = mapped_column(index=True)
    # "osm:node/123", "google_places:<place_id>", "csv:<sha1>", "n8n:<sha1>": unique per lead.
    source_key: Mapped[str] = mapped_column(unique=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType)

    name: Mapped[str]
    phone: Mapped[str | None] = mapped_column(index=True)
    email: Mapped[str | None]
    website: Mapped[str | None]
    website_domain: Mapped[str | None] = mapped_column(index=True)
    address: Mapped[str | None]
    area: Mapped[str | None]
    city: Mapped[str | None]
    country: Mapped[str | None]
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    category: Mapped[str | None]
    services: Mapped[list[str]] = mapped_column(JSONType, default=list)
    established_year: Mapped[int | None]
    verified: Mapped[bool | None]
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None]
    business_status: Mapped[str | None]

    enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    enriched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    score: Mapped[int | None]
    # Indexed for `?status=` on the rare statuses (in_review, escalated); a common one is
    # served by the first_seen_at index instead.
    status: Mapped[LeadStatus] = mapped_column(
        str_enum(LeadStatus, "status"), default=LeadStatus.NEW, index=True
    )
    crm_record_id: Mapped[str | None]
    opt_out: Mapped[bool] = mapped_column(server_default=false())
    # The lead list's default sort (newest first).
    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    """One execution of a campaign: collect leads, then process each one."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    trigger: Mapped[RunTrigger] = mapped_column(str_enum(RunTrigger, "trigger"))
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Copied from the campaign at start, so editing the campaign later doesn't change old runs.
    rubric_id: Mapped[int] = mapped_column(ForeignKey("scoring_rubrics.id"))
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "status"), default=RunStatus.QUEUED
    )
    callback_url: Mapped[str | None]
    correlation_id: Mapped[str | None]
    # {"collected": 20, "new": 18, "duplicates": 2, ...}
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RunItem(Base):
    """One lead inside one run. Has its own LangGraph thread (`thread_id`)."""

    __tablename__ = "run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "lead_id"),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_range"),
        # The approval queue: items waiting for a human, oldest first.
        Index(
            "ix_run_items_awaiting_approval",
            "created_at",
            postgresql_where=text("status = 'awaiting_approval'"),
            sqlite_where=text("status = 'awaiting_approval'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    # A lead's history; run_id needs no index of its own (unique (run_id, lead_id) has it first).
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    thread_id: Mapped[str] = mapped_column(unique=True)
    status: Mapped[RunItemStatus] = mapped_column(
        str_enum(RunItemStatus, "status"), default=RunItemStatus.PENDING
    )
    route: Mapped[Route | None] = mapped_column(str_enum(Route, "route"))
    extraction_ok: Mapped[bool | None]
    # ["possible_duplicate:12", "category_mismatch", ...]
    flags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    score: Mapped[int | None]
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


class RunStep(Base):
    """One graph node executing for one run item (written by `@tracked_node`)."""

    __tablename__ = "run_steps"
    # A run item's step timeline (and the reports' look-up of an item's gate step).
    __table_args__ = (Index("ix_run_steps_run_item_id_started_at", "run_item_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_item_id: Mapped[int] = mapped_column(ForeignKey("run_items.id"))
    node: Mapped[str]
    status: Mapped[StepStatus] = mapped_column(str_enum(StepStatus, "status"))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int | None]
    trace_id: Mapped[str | None]


class Approval(Base):
    """A human's decision on one run item. The unique `run_item_id` stops double decisions."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision = 'approved' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="reason_required_for_reject",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_item_id: Mapped[int] = mapped_column(ForeignKey("run_items.id"), unique=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[Decision] = mapped_column(str_enum(Decision, "decision"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class AuditLog(Base):
    """Append-only record of everything that happened. On Postgres a trigger blocks edits.

    `run_id` / `run_item_id` / `entity_id` are plain numbers, not foreign keys, so the
    audit trail never blocks (or depends on) changes to the rows it describes.
    """

    __tablename__ = "audit_log"
    # The audit log filtered by entity (one lead's or one item's story).
    __table_args__ = (Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),)

    # BIGINT on Postgres (this table grows fastest); SQLite only auto-increments INTEGER.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    # Newest first, and `?from=` / `?to=` time ranges.
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "actor_type"))
    # A user's email, a node name ("score"), or "worker"
    actor: Mapped[str]
    # e.g. "lead.collected", "approval.approved", "crm.written"
    action: Mapped[str]
    entity_type: Mapped[str]
    entity_id: Mapped[int | None]
    run_id: Mapped[int | None] = mapped_column(index=True)
    run_item_id: Mapped[int | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    correlation_id: Mapped[str | None]


class Job(Base):
    """A unit of work for the worker. The API only writes these; the worker claims them."""

    __tablename__ = "jobs"
    __table_args__ = (
        # The worker's claim query: queued jobs that are due, oldest first.
        Index(
            "ix_jobs_queued_run_after",
            "run_after",
            "id",
            postgresql_where=text("status = 'queued'"),
            sqlite_where=text("status = 'queued'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[JobType] = mapped_column(str_enum(JobType, "type"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "status"), default=JobStatus.QUEUED
    )
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(server_default=text("3"))
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locked_by: Mapped[str | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


# The lead list's `sort=-score` (best first, unscored last). A plain index on score read
# backwards would put the unscored (NULL) leads first, so the index says DESC NULLS LAST.
# Postgres-only: SQLite can't write NULLS LAST in an index (and has no need for it in tests).
Index("ix_leads_score", Lead.score.desc().nulls_last()).ddl_if(dialect="postgresql")
POSTGRES_ONLY_INDEXES = {"ix_leads_score"}

# `ix_leads_name_trgm` needs the pg_trgm extension. The migration creates it; this makes
# `Base.metadata.create_all()` (some Postgres tests) create it too.
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm").execute_if(dialect="postgresql"),
)
