"""Measure the API's real queries with EXPLAIN ANALYZE, with and without each index.

The SQL phase's tool (PLAN §13, Phase 9). It backs every index in the migration
with a plan and a timing, written to a Markdown file that `docs/sql-notes.md` quotes.

    python scripts/sql_explain.py load --leads 50000      # once: a fresh bench DB
    python scripts/sql_explain.py explain --out plans.md  # measure every case

`load` drops and re-creates the database `leadgate_sqlbench` on the server that
`TEST_POSTGRES_URL` (or `--server-url`) points at, migrates it to head and fills it with
`fake-data --leads N --history`. It never touches the app's own database.

`explain` runs each case below twice, each time inside a transaction that is rolled
back: once with the case's index dropped ("without") and once with it created
("with"). So the bench DB is left as the migration made it, and an index that isn't in
the migration (a candidate that didn't make it) can still be measured. Each query runs
once to warm the cache, then 5 times; the fastest `Execution Time` counts.

Index DDL comes from the models when the index is in them (the source of truth), else
from `CANDIDATES` below (indexes that were measured and left out). A case with no
index (most reports) is measured once.
"""

import argparse
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, Executable, create_engine, func, make_url, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.agents.nodes.check import same_contact_query
from app.api.routes.approvals import queue_query
from app.api.routes.audit import audit_query
from app.api.routes.leads import lead_history_query, leads_query
from app.api.routes.runs import run_items_query, steps_query
from app.db.base import Base
from app.db.fake_data import insert_fake_leads
from app.db.fake_history import insert_fake_history
from app.db.models import Lead, LeadStatus, Run, RunItem
from app.db.session import make_session_factory
from app.reporting.queries import REPORTS, ReportName, report_params
from app.worker.jobs import next_job_query

BACKEND_DIR = Path(__file__).resolve().parents[1]
BENCH_DB = "leadgate_sqlbench"
TRIES = 5
PAGE = 50

# Indexes that were measured and left out of the migration (see docs/sql-notes.md).
CANDIDATES: dict[str, str] = {
    "ix_run_items_run_id": "CREATE INDEX ix_run_items_run_id ON run_items (run_id)",
    "ix_runs_campaign_id": "CREATE INDEX ix_runs_campaign_id ON runs (campaign_id)",
}


@dataclass
class Case:
    name: str
    what: str
    indexes: list[str]
    # Builds the statement from a few ids looked up in the bench data.
    query: Callable[[Sample], Executable]
    # Tables to re-ANALYZE after adding or dropping the index.
    tables: list[str]


@dataclass
class Sample:
    """Real ids and values from the bench data, so each query finds something."""

    now: datetime
    run_id: int
    item_id: int
    lead_id: int
    campaign_id: int
    lead: Lead
    phones: list[str]


def page(stmt: Any) -> Any:
    return stmt.limit(PAGE)


def count(stmt: Any) -> Any:
    """The `total` that `paginate()` adds to every list answer."""
    return select(func.count()).select_from(stmt.order_by(None).subquery())


CASES = [
    Case(
        "approval_queue",
        "GET /api/approvals/queue: items waiting for a human, oldest first (page 1)",
        ["ix_run_items_awaiting_approval"],
        lambda s: page(queue_query()),
        ["run_items"],
    ),
    Case(
        "approval_queue_count",
        "GET /api/approvals/queue: the total",
        ["ix_run_items_awaiting_approval"],
        lambda s: count(queue_query()),
        ["run_items"],
    ),
    Case(
        "worker_claim",
        "the worker's claim: the oldest due queued job",
        ["ix_jobs_queued_run_after"],
        lambda s: next_job_query(s.now),
        ["jobs"],
    ),
    Case(
        "run_items",
        "GET /api/runs/{id}: the run's items (unique (run_id, lead_id) already indexes run_id)",
        ["ix_run_items_run_id"],
        lambda s: run_items_query(s.run_id),
        ["run_items"],
    ),
    Case(
        "lead_history",
        "GET /api/leads/{id}: the lead's run items, newest first",
        ["ix_run_items_lead_id"],
        lambda s: lead_history_query(s.lead_id),
        ["run_items"],
    ),
    Case(
        "leads_by_campaign",
        "GET /api/leads?campaign_id=: leads in the campaign's runs (page 1)",
        ["ix_runs_campaign_id"],
        lambda s: page(leads_query(campaign_id=s.campaign_id)),
        ["runs"],
    ),
    Case(
        "step_timeline",
        "GET /api/run-items/{id}: the item's steps in order",
        ["ix_run_steps_run_item_id_started_at"],
        lambda s: steps_query(s.item_id),
        ["run_steps"],
    ),
    Case(
        "audit_by_run",
        "GET /api/audit?run_id=: one run's audit rows, newest first (page 1)",
        ["ix_audit_log_run_id"],
        lambda s: page(audit_query(run_id=s.run_id)),
        ["audit_log"],
    ),
    Case(
        "audit_by_entity",
        "GET /api/audit?entity_type=lead&entity_id=: one lead's audit rows (page 1)",
        ["ix_audit_log_entity_type_entity_id"],
        lambda s: page(audit_query(entity_type="lead", entity_id=s.lead_id)),
        ["audit_log"],
    ),
    Case(
        "audit_latest",
        "GET /api/audit: the newest audit rows (page 1)",
        ["ix_audit_log_occurred_at"],
        lambda s: page(audit_query()),
        ["audit_log"],
    ),
    Case(
        "audit_last_day",
        "GET /api/audit?from=: the last 24 hours (page 1)",
        ["ix_audit_log_occurred_at"],
        lambda s: page(audit_query(occurred_from=s.now - timedelta(days=1))),
        ["audit_log"],
    ),
    Case(
        "audit_last_day_count",
        "GET /api/audit?from=: the total for the last 24 hours",
        ["ix_audit_log_occurred_at"],
        lambda s: count(audit_query(occurred_from=s.now - timedelta(days=1))),
        ["audit_log"],
    ),
    Case(
        "leads_newest",
        "GET /api/leads: newest first, the default (page 1)",
        ["ix_leads_first_seen_at"],
        lambda s: page(leads_query()),
        ["leads"],
    ),
    Case(
        "leads_best_score",
        "GET /api/leads?sort=-score: best score first (page 1)",
        ["ix_leads_score"],
        lambda s: page(leads_query(sort="-score")),
        ["leads"],
    ),
    Case(
        "leads_in_review",
        "GET /api/leads?status=in_review (a rare status, page 1)",
        ["ix_leads_status"],
        lambda s: page(leads_query(status=LeadStatus.IN_REVIEW)),
        ["leads"],
    ),
    Case(
        "leads_approved",
        "GET /api/leads?status=approved (a common status, page 1)",
        ["ix_leads_status"],
        lambda s: page(leads_query(status=LeadStatus.APPROVED)),
        ["leads"],
    ),
    Case(
        "leads_name_search",
        "GET /api/leads?q=: part of the name, ILIKE '%...%' (page 1)",
        ["ix_leads_name_trgm"],
        lambda s: page(leads_query(q="harris")),
        ["leads"],
    ),
    Case(
        "leads_name_search_count",
        "GET /api/leads?q=: the total",
        ["ix_leads_name_trgm"],
        lambda s: count(leads_query(q="harris")),
        ["leads"],
    ),
    Case(
        "duplicate_check",
        "the check node: other leads with the same source ref, phone or website domain",
        ["ix_leads_source_ref", "ix_leads_phone", "ix_leads_website_domain"],
        lambda s: same_contact_query(s.lead, s.phones),
        ["leads"],
    ),
]


def report(name: ReportName) -> Callable[[Sample], Executable]:
    """A report's SQL with the API's default parameters (last 30 days, top 3)."""
    found = REPORTS[name]

    def build(sample: Sample) -> Executable:
        params = report_params(found, sample.now.date(), days=30, top=3)
        return text(found.sql).bindparams(**params)

    return build


CASES += [
    Case(
        "report_daily_funnel",
        "GET /api/reports/daily_funnel: the last 30 days of audit rows, per day",
        ["ix_audit_log_occurred_at"],
        report(ReportName.DAILY_FUNNEL),
        ["audit_log"],
    ),
    Case(
        "report_reviewer_stats",
        "GET /api/reports/reviewer_stats: each approval's gate step (one grouped join)",
        ["ix_run_steps_run_item_id_started_at"],
        report(ReportName.REVIEWER_STATS),
        ["run_steps"],
    ),
    *[
        Case(
            f"report_{name}",
            f"GET /api/reports/{name} (no index: reads the whole table)",
            [],
            report(name),
            [],
        )
        for name in (
            ReportName.APPROVED_RUNNING_TOTAL,
            ReportName.TOP_LEADS_PER_AREA,
            ReportName.NODE_DURATIONS,
            ReportName.SCORE_DISTRIBUTION,
            ReportName.CAMPAIGN_CONVERSION,
        )
    ],
]


def index_ddl(name: str) -> str:
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            if index.name == name:
                return str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    return CANDIDATES[name]


def as_sql(stmt: Executable) -> str:
    """The statement as Postgres SQL with its values written in (EXPLAIN takes no binds)."""
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    # The pyformat dialect doubles "%" for the driver; we send the text as-is.
    return sql.replace("%%", "%")


def explain(conn: Connection, sql: str) -> tuple[float, str]:
    """(fastest execution time in ms, the plan text) over TRIES runs after a warm-up."""
    driver = conn.connection.driver_connection
    best, plan = float("inf"), ""
    for _ in range(TRIES + 1):
        with driver.cursor() as cursor:
            cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) {sql}")
            lines = [row[0] for row in cursor.fetchall()]
        ms = float(re.search(r"Execution Time: ([\d.]+) ms", lines[-1]).group(1))
        if ms < best:
            best, plan = ms, "\n".join(lines)
    return best, plan


def measure(engine: Engine, case: Case, sample: Sample, with_index: bool) -> tuple[float, str]:
    sql = as_sql(case.query(sample))
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            for name in case.indexes:
                ddl = index_ddl(name).replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1)
                conn.exec_driver_sql(ddl if with_index else f"DROP INDEX IF EXISTS {name}")
            for table in case.tables:
                conn.exec_driver_sql(f"ANALYZE {table}")
            return explain(conn, sql)
        finally:
            transaction.rollback()


def pick_sample(engine: Engine) -> Sample:
    """A typical run (the median id), one of its items and its lead, and that lead's phone."""
    with make_session_factory(engine)() as session:
        run_ids = session.scalars(select(Run.id).order_by(Run.id)).all()
        run = session.get(Run, run_ids[len(run_ids) // 2])
        item = session.scalars(select(RunItem).where(RunItem.run_id == run.id).limit(1)).one()
        lead = session.get(Lead, item.lead_id)
        session.expunge(lead)
        return Sample(
            now=datetime.now(UTC),
            run_id=run.id,
            item_id=item.id,
            lead_id=lead.id,
            campaign_id=run.campaign_id,
            lead=lead,
            phones=[lead.phone] if lead.phone else ["+15125550100"],
        )


def bench_url(server_url: str) -> str:
    return make_url(server_url).set(database=BENCH_DB).render_as_string(hide_password=False)


def cmd_load(args: argparse.Namespace) -> int:
    admin = create_engine(args.server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BENCH_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{BENCH_DB}"'))
    admin.dispose()

    url = bench_url(args.server_url)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    started = datetime.now(UTC)
    with make_session_factory(engine)() as session:
        print(f"inserted {insert_fake_leads(session, args.leads, seed=args.seed)} fake leads")
        print(f"history: {insert_fake_history(session, seed=args.seed).summary()}")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM ANALYZE")
    engine.dispose()
    print(f"loaded {BENCH_DB} in {(datetime.now(UTC) - started).seconds} s")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    engine = create_engine(bench_url(args.server_url))
    sample = pick_sample(engine)
    cases = [case for case in CASES if not args.case or case.name in args.case]
    out = [f"# EXPLAIN ANALYZE results ({sample.now:%Y-%m-%d %H:%M} UTC)", ""]
    out.append(table_sizes(engine))
    summary = ["| case | index | without | with |", "|---|---|---|---|"]
    for case in cases:
        without_ms, without_plan = measure(engine, case, sample, with_index=False)
        if case.indexes:
            with_ms, with_plan = measure(engine, case, sample, with_index=True)
        else:  # nothing to add: one measurement
            with_ms, with_plan = without_ms, without_plan
        indexes = ", ".join(case.indexes)
        summary.append(f"| {case.name} | {indexes} | {without_ms:.3f} ms | {with_ms:.3f} ms |")
        print(f"{case.name:26} {without_ms:10.3f} ms -> {with_ms:10.3f} ms   {indexes}")
        out += [
            f"## {case.name}",
            "",
            case.what,
            "",
            f"Index: `{indexes}`",
            "",
            "```sql",
            as_sql(case.query(sample)),
            "```",
            "",
            f"Without the index: **{without_ms:.3f} ms**",
            "",
            "```",
            without_plan,
            "```",
            "",
            f"With the index: **{with_ms:.3f} ms**",
            "",
            "```",
            with_plan,
            "```",
            "",
        ]
    engine.dispose()
    out[3:3] = ["## Summary", "", *summary, ""]
    Path(args.out).write_text("\n".join(out))
    print(f"wrote {args.out}")
    return 0


def table_sizes(engine: Engine) -> str:
    tables = ["leads", "runs", "run_items", "run_steps", "approvals", "audit_log", "jobs"]
    with engine.connect() as conn:
        rows = [
            f"| {table} | {conn.scalar(text(f'SELECT count(*) FROM {table}')):,} |"
            for table in tables
        ]
    return "\n".join(["| table | rows |", "|---|---|", *rows, ""])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--server-url",
        default=os.environ.get("TEST_POSTGRES_URL"),
        help="a Postgres URL whose user may create databases (default: TEST_POSTGRES_URL)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load", help=f"re-create {BENCH_DB} with fake data")
    load.add_argument("--leads", type=int, default=50_000)
    load.add_argument("--seed", type=int, default=9)
    load.set_defaults(handler=cmd_load)
    explain_parser = commands.add_parser("explain", help="measure every case")
    explain_parser.add_argument("--out", default="sql_explain.md", help="the Markdown report")
    explain_parser.add_argument("--case", action="append", help="only this case (repeatable)")
    explain_parser.set_defaults(handler=cmd_explain)
    args = parser.parse_args()
    if not args.server_url:
        parser.error("set TEST_POSTGRES_URL or pass --server-url")
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
