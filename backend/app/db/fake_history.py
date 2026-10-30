"""Fake run history for the fake leads: campaigns, runs, run items, steps, approvals,
audit rows and jobs, so the SQL phase has realistic volume to measure.

`python -m app.cli fake-data --leads N --history` calls `insert_fake_history()` after
`insert_fake_leads()`. Every fake lead that isn't in a run yet gets one run item and the
rows the real worker would have left behind (see `app.agents.tracking` for the steps
and audit rows, `app.services.approvals` for the decision):

- one campaign per category + city ("dentist in Austin (fake)"), one run per campaign
  and day the leads were first seen
- per item: steps enrich → extract → check → score → approval_gate (→ route → act),
  about 10 audit rows, a `process_item` job (and a `resume_item` job once decided)
- a few fake reviewers decide each item after a wait (some decide faster than others);
  a decision that would land after `now` hasn't happened yet, so that item still waits
  at the gate, like a real queue
- a few items fail in `extract`; about 1 in 10 carries a `possible_duplicate` flag

The lead's own `status` and `score` are rewritten to match its item (approved,
rejected, escalated, in_review, or new for a failed item). 50k leads give about
500k audit rows. Rows are inserted in batches of runs and committed per batch.
"""

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.agents.tracking import NODE_ACTIONS
from app.auth.security import hash_password
from app.db.models import (
    ActorType,
    Approval,
    AuditLog,
    Campaign,
    Decision,
    Job,
    JobStatus,
    JobType,
    Lead,
    LeadStatus,
    Route,
    Run,
    RunItem,
    RunItemStatus,
    RunStatus,
    RunStep,
    RunTrigger,
    ScoringRubric,
    StepStatus,
    User,
    UserRole,
)
from app.db.seed import GENERAL_B2B_RULES

# How many runs are inserted (and committed) together.
RUNS_PER_BATCH = 50
# reviewer email -> their median wait before deciding, in minutes
REVIEWERS = {
    "reviewer.ana@example.com": 25,
    "reviewer.bikash@example.com": 60,
    "reviewer.chen@example.com": 150,
    "reviewer.dana@example.com": 400,
}
# node -> (shortest, longest) time spent in it, in milliseconds
NODE_MS = {
    "enrich": (600, 9000),
    "extract": (1200, 12000),
    "check": (5, 120),
    "score": (500, 5000),
    "approval_gate": (2, 40),
    "route": (1, 10),
    "act": (150, 2500),
}
FAIL_SHARE = 0.02
DUPLICATE_SHARE = 0.1
ESCALATE_SHARE = 0.08
REJECT_REASONS = [
    "not our kind of client",
    "already a customer",
    "duplicate of another lead",
    "website looks fine already",
    "too far away",
]


@dataclass
class HistoryCounts:
    campaigns: int = 0
    runs: int = 0
    run_items: int = 0
    run_steps: int = 0
    approvals: int = 0
    audit_rows: int = 0
    jobs: int = 0

    def summary(self) -> str:
        return ", ".join(f"{count} {name}" for name, count in vars(self).items())


@dataclass
class ItemPlan:
    """Everything about one run item, worked out before any id exists."""

    lead: dict[str, Any]
    status: RunItemStatus
    score: int | None
    flags: list[str]
    created_at: datetime
    # (node, status, started_at, finished_at, duration_ms, output)
    steps: list[tuple[str, StepStatus, datetime, datetime | None, int | None, dict]] = field(
        default_factory=list
    )
    reviewer: str | None = None
    decision: Decision | None = None
    reason: str | None = None
    decided_at: datetime | None = None
    route: Route | None = None
    notified_at: datetime | None = None
    error: str | None = None
    finished_at: datetime | None = None


@dataclass
class RunPlan:
    campaign_id: int
    trigger: RunTrigger
    created_at: datetime
    items: list[ItemPlan]

    @property
    def correlation_id(self) -> str:
        return f"fake-{self.campaign_id}-{self.created_at:%Y%m%d}"

    @property
    def status(self) -> RunStatus:
        waiting = any(item.status == RunItemStatus.AWAITING_APPROVAL for item in self.items)
        return RunStatus.WAITING_APPROVAL if waiting else RunStatus.COMPLETED

    @property
    def finished_at(self) -> datetime | None:
        if self.status != RunStatus.COMPLETED:
            return None
        return max(item.finished_at or item.created_at for item in self.items)


def insert_fake_history(
    session: Session, now: datetime | None = None, seed: int | None = None
) -> HistoryCounts:
    """Give every fake lead without a run item a run history (see the module docstring)."""
    now = now or datetime.now(UTC)
    rnd = random.Random(seed)
    counts = HistoryCounts()

    in_a_run = select(RunItem.lead_id)
    leads = [
        dict(row._mapping)
        for row in session.execute(
            select(Lead.id, Lead.category, Lead.city, Lead.score, Lead.first_seen_at, Lead.email)
            .where(Lead.source == "fake", Lead.id.not_in(in_a_run))
            .order_by(Lead.first_seen_at, Lead.id)
        )
    ]
    if not leads:
        return counts

    reviewers = fake_reviewers(session)
    rubric_id = fake_rubric(session)
    campaigns = fake_campaigns(
        session, rubric_id, {(lead["category"], lead["city"]) for lead in leads}
    )
    counts.campaigns = len(campaigns)

    # One run per campaign and day: the leads it found that day.
    by_run: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for lead in leads:
        campaign_id = campaigns[(lead["category"], lead["city"])]
        by_run[(campaign_id, f"{lead['first_seen_at']:%Y-%m-%d}")].append(lead)

    plans = [
        plan_run(rnd, campaign_id, run_leads, now)
        for (campaign_id, _day), run_leads in sorted(
            by_run.items(), key=lambda entry: entry[1][0]["first_seen_at"]
        )
    ]
    for start in range(0, len(plans), RUNS_PER_BATCH):
        insert_runs(session, plans[start : start + RUNS_PER_BATCH], reviewers, rubric_id, counts)
        session.commit()
    return counts


def fake_reviewers(session: Session) -> dict[str, int]:
    """The fake reviewers' ids by email, created on first use.

    They share one password hash of a random secret nobody knows, so nobody can log in
    as them (create real reviewers through the API).
    """
    existing = dict(
        session.execute(select(User.email, User.id).where(User.email.in_(REVIEWERS))).all()
    )
    missing = [email for email in REVIEWERS if email not in existing]
    if missing:
        unusable = hash_password(random.SystemRandom().randbytes(16).hex())
        for email in missing:
            user = User(email=email, password_hash=unusable, role=UserRole.REVIEWER)
            session.add(user)
            session.flush()
            existing[email] = user.id
    return existing


def fake_rubric(session: Session) -> int:
    """The newest `general-b2b` rubric (created as v1 if `seed` hasn't run)."""
    rubric_id = session.scalar(
        select(ScoringRubric.id)
        .where(ScoringRubric.name == "general-b2b")
        .order_by(ScoringRubric.version.desc())
        .limit(1)
    )
    if rubric_id is None:
        rubric = ScoringRubric(name="general-b2b", version=1, rules=GENERAL_B2B_RULES)
        session.add(rubric)
        session.flush()
        rubric_id = rubric.id
    return rubric_id


def fake_campaigns(
    session: Session, rubric_id: int, pairs: set[tuple[str, str]]
) -> dict[tuple[str, str], int]:
    """One campaign per (category, city), reused if it already exists."""
    campaigns = {}
    for category, city in sorted(pairs):
        name = f"{category} in {city} (fake)"
        campaign_id = session.scalar(select(Campaign.id).where(Campaign.name == name))
        if campaign_id is None:
            campaign = Campaign(
                name=name,
                niche=category,
                location=f"{city}, TX",
                sources=["osm"],
                source_options={"max_results": 50},
                rubric_id=rubric_id,
            )
            session.add(campaign)
            session.flush()
            campaign_id = campaign.id
        campaigns[(category, city)] = campaign_id
    return campaigns


def plan_run(
    rnd: random.Random, campaign_id: int, leads: list[dict[str, Any]], now: datetime
) -> RunPlan:
    created_at = leads[0]["first_seen_at"] - timedelta(minutes=1)
    trigger = RunTrigger.N8N if rnd.random() < 0.3 else RunTrigger.SCHEDULE
    return RunPlan(
        campaign_id=campaign_id,
        trigger=trigger,
        created_at=created_at,
        items=[plan_item(rnd, lead, now) for lead in leads],
    )


def plan_item(rnd: random.Random, lead: dict[str, Any], now: datetime) -> ItemPlan:
    """The item's story: its steps, and (unless it failed or still waits) the decision."""
    start = lead["first_seen_at"]
    item = ItemPlan(lead=lead, status=RunItemStatus.DONE, score=None, flags=[], created_at=start)
    moment = start + timedelta(seconds=2)

    def step(node: str, output: dict[str, Any], status: StepStatus = StepStatus.COMPLETED) -> None:
        nonlocal moment
        low, high = NODE_MS[node]
        duration = rnd.randint(low, high)
        finished = moment + timedelta(milliseconds=duration)
        item.steps.append((node, status, moment, finished, duration, output))
        moment = finished

    step("enrich", {"enrichment": {"emails": [lead["email"]] if lead["email"] else []}})
    if rnd.random() < FAIL_SHARE:
        item.status = RunItemStatus.FAILED
        item.error = "ValidationError: the model's answer failed the checks 3 times"
        step("extract", {}, StepStatus.FAILED)
        item.finished_at = moment
        return item

    step("extract", {"extraction_ok": True, "extraction_issues": []})
    if rnd.random() < DUPLICATE_SHARE:
        item.flags.append(f"possible_duplicate:{rnd.randint(1, max(lead['id'] - 1, 1))}")
    step("check", {"flags": item.flags})
    item.score = lead["score"] if lead["score"] is not None else rnd.randint(0, 100)
    step("score", {"score": item.score, "score_breakdown": [], "score_explanation": "fake"})

    # The gate: paused until a reviewer decides.
    gate_start = moment
    item.reviewer = rnd.choice(list(REVIEWERS))
    wait = timedelta(minutes=REVIEWERS[item.reviewer] * rnd.lognormvariate(0, 0.8))
    decided_at = gate_start + wait
    if decided_at > now:
        item.status = RunItemStatus.AWAITING_APPROVAL
        item.reviewer = None
        item.steps.append(("approval_gate", StepStatus.STARTED, gate_start, None, None, {}))
        return item

    approve_chance = 0.2 + 0.6 * item.score / 100
    item.decision = Decision.APPROVED if rnd.random() < approve_chance else Decision.REJECTED
    item.decided_at = decided_at
    moment = decided_at
    gate_ms = rnd.randint(*NODE_MS["approval_gate"])
    moment += timedelta(milliseconds=gate_ms)
    item.steps.append(
        (
            "approval_gate",
            StepStatus.COMPLETED,
            gate_start,
            moment,
            gate_ms,
            {"decision": item.decision.value},
        )
    )
    if item.decision == Decision.REJECTED:
        item.reason = rnd.choice(REJECT_REASONS)
        item.route = Route.SKIP
    elif item.flags or rnd.random() < ESCALATE_SHARE:
        item.route = Route.ESCALATE
    else:
        item.route = Route.NOTIFY
    step("route", {"route": item.route.value})
    if item.route != Route.SKIP:
        crm = f"run-item-{lead['id']}" if item.route == Route.NOTIFY else None
        step("act", {"crm_record_id": crm, "notified": True})
        item.notified_at = moment
    item.finished_at = moment
    return item


def lead_status(item: ItemPlan) -> LeadStatus:
    if item.status == RunItemStatus.AWAITING_APPROVAL:
        return LeadStatus.IN_REVIEW
    if item.route == Route.NOTIFY:
        return LeadStatus.APPROVED
    if item.route == Route.ESCALATE:
        return LeadStatus.ESCALATED
    if item.route == Route.SKIP:
        return LeadStatus.REJECTED
    return LeadStatus.NEW  # failed before scoring


def insert_runs(
    session: Session,
    plans: list[RunPlan],
    reviewers: dict[str, int],
    rubric_id: int,
    counts: HistoryCounts,
) -> None:
    """Insert one batch of planned runs and everything under them."""
    run_ids = session.scalars(
        insert(Run).returning(Run.id, sort_by_parameter_order=True),
        [run_row(plan, rubric_id) for plan in plans],
    ).all()
    runs = list(zip(run_ids, plans, strict=True))
    items = [(run_id, plan, item) for run_id, plan in runs for item in plan.items]
    item_ids = session.scalars(
        insert(RunItem).returning(RunItem.id, sort_by_parameter_order=True),
        [item_row(run_id, item) for run_id, _plan, item in items],
    ).all()

    rows: dict[type, list[dict[str, Any]]] = defaultdict(list)
    for run_id, plan in runs:
        add_run_rows(rows, run_id, plan)
    for item_id, (run_id, plan, item) in zip(item_ids, items, strict=True):
        ids = Ids(run_id, item_id, item.lead["id"], plan.correlation_id)
        add_item_rows(rows, ids, item)
        if item.decision is not None:
            add_decision_rows(rows, ids, item, reviewers)

    for model in (RunStep, Approval, AuditLog, Job):
        if rows[model]:
            session.execute(insert(model), rows[model])
    # Bulk UPDATE by primary key: the leads' status and score follow their items.
    session.execute(
        update(Lead),
        [
            {"id": item.lead["id"], "status": lead_status(item), "score": item.score}
            for *_, item in items
        ],
    )

    counts.runs += len(run_ids)
    counts.run_items += len(item_ids)
    counts.run_steps += len(rows[RunStep])
    counts.approvals += len(rows[Approval])
    counts.audit_rows += len(rows[AuditLog])
    counts.jobs += len(rows[Job])


@dataclass
class Ids:
    """The ids an item's rows point to, known once the run and the item are inserted."""

    run_id: int
    item_id: int
    lead_id: int
    correlation_id: str


def run_row(plan: RunPlan, rubric_id: int) -> dict[str, Any]:
    return {
        "campaign_id": plan.campaign_id,
        "trigger": plan.trigger,
        "rubric_id": rubric_id,
        "status": plan.status,
        "correlation_id": plan.correlation_id,
        "stats": {
            "collected": len(plan.items),
            "new": len(plan.items),
            "items": dict(Counter(item.status.value for item in plan.items)),
        },
        "created_at": plan.created_at,
        "started_at": plan.created_at + timedelta(seconds=5),
        "finished_at": plan.finished_at,
    }


def item_row(run_id: int, item: ItemPlan) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "lead_id": item.lead["id"],
        "thread_id": f"run-{run_id}-lead-{item.lead['id']}",
        "status": item.status,
        "route": item.route,
        "extraction_ok": None if item.status == RunItemStatus.FAILED else True,
        "flags": item.flags,
        "score": item.score,
        "notified_at": item.notified_at,
        "error": item.error,
        "created_at": item.created_at,
        "updated_at": item.finished_at or item.steps[-1][2],
    }


def add_run_rows(rows: dict[type, list[dict[str, Any]]], run_id: int, plan: RunPlan) -> None:
    """The run's own audit rows and its `start_run` job."""
    started_at = plan.created_at + timedelta(seconds=5)
    events = [("run.created", plan.created_at), ("run.started", started_at)]
    if plan.finished_at is not None:
        events.append(("run.completed", plan.finished_at))
    for action, moment in events:
        rows[AuditLog].append(
            audit_row(
                action,
                moment,
                ActorType.SYSTEM,
                "worker",
                ("run", run_id),
                Ids(run_id, 0, 0, plan.correlation_id),
                {"campaign_id": plan.campaign_id},
            )
        )
    rows[Job].append(
        job_row(JobType.START_RUN, {"run_id": run_id}, plan.created_at, JobStatus.DONE)
    )


def add_item_rows(rows: dict[type, list[dict[str, Any]]], ids: Ids, item: ItemPlan) -> None:
    """The item's steps, the audit rows `@tracked_node` writes for them, its job."""
    lead = ("lead", ids.lead_id)
    rows[AuditLog].append(
        audit_row("lead.collected", item.created_at, ActorType.SYSTEM, "worker", lead, ids, {})
    )
    failed = item.status == RunItemStatus.FAILED
    rows[Job].append(
        job_row(
            JobType.PROCESS_ITEM,
            {"run_id": ids.run_id, "run_item_id": ids.item_id},
            item.created_at,
            JobStatus.FAILED if failed else JobStatus.DONE,
            attempts=3 if failed else 1,
        )
    )
    for node, status, started_at, finished_at, duration_ms, output in item.steps:
        rows[RunStep].append(
            {
                "run_item_id": ids.item_id,
                "node": node,
                "status": status,
                "input": {},
                "output": output,
                "error": item.error if status == StepStatus.FAILED else None,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
            }
        )
        if node == "approval_gate":
            requested = {"score": item.score}
            events = [("approval.requested", started_at, requested)]
            if status == StepStatus.COMPLETED:
                events.append(("lead.decided", finished_at, output))
        elif status == StepStatus.FAILED:
            events = [("node.failed", finished_at, {"node": node})]
        else:
            events = [(NODE_ACTIONS[node], finished_at, output)]
            if node == "act" and output["crm_record_id"]:
                events.append(
                    ("crm.written", finished_at, {"crm_record_id": output["crm_record_id"]})
                )
        for action, moment, details in events:
            rows[AuditLog].append(
                audit_row(action, moment, ActorType.AGENT, node, lead, ids, details)
            )


def add_decision_rows(
    rows: dict[type, list[dict[str, Any]]], ids: Ids, item: ItemPlan, reviewers: dict[str, int]
) -> None:
    """The reviewer's approval row, its audit row and the `resume_item` job."""
    rows[Approval].append(
        {
            "run_item_id": ids.item_id,
            "reviewer_id": reviewers[item.reviewer],
            "decision": item.decision,
            "reason": item.reason,
            "created_at": item.decided_at,
        }
    )
    rows[AuditLog].append(
        audit_row(
            f"approval.{item.decision.value}",
            item.decided_at,
            ActorType.USER,
            item.reviewer,
            ("run_item", ids.item_id),
            ids,
            {"lead_id": ids.lead_id, "reason": item.reason},
        )
    )
    payload = {"run_id": ids.run_id, "run_item_id": ids.item_id}
    rows[Job].append(job_row(JobType.RESUME_ITEM, payload, item.decided_at, JobStatus.DONE))


def audit_row(
    action: str,
    moment: datetime,
    actor_type: ActorType,
    actor: str,
    entity: tuple[str, int],
    ids: Ids,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "occurred_at": moment,
        "actor_type": actor_type,
        "actor": actor,
        "action": action,
        "entity_type": entity[0],
        "entity_id": entity[1],
        "run_id": ids.run_id,
        "run_item_id": ids.item_id or None,
        "details": details,
        "correlation_id": ids.correlation_id,
    }


def job_row(
    job_type: JobType,
    payload: dict[str, Any],
    moment: datetime,
    status: JobStatus,
    attempts: int = 1,
) -> dict[str, Any]:
    return {
        "type": job_type,
        "payload": payload,
        "status": status,
        "attempts": attempts,
        "run_after": moment,
        "locked_at": moment,
        "locked_by": "fake-worker",
        "created_at": moment,
        "updated_at": moment,
    }
