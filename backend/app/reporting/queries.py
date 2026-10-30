"""The reports (PLAN §6): hand-written Postgres SQL, run with `text()`.

Each report is a `Report`: a name, one line saying what it shows, the SQL and the
parameters it takes. `run_report()` runs one and hands back its rows as plain dicts.
`GET /api/reports/{name}` (`app.api.routes.reports`) is the only caller in the app;
`scripts/sql_explain.py` measures them too.

They are Postgres-only on purpose (window functions, `FILTER`, `percentile_cont`,
`generate_series`, JSONB), so they are tested on Postgres (`tests/integration/`), not
SQLite. Times are grouped by UTC day. Numbers that would come back as `numeric` are
cast to `float8` (or `int`), so the JSON answer has plain numbers, not strings.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# One row per UTC day from :first_day to :last_day, so days with no activity show 0.
DAILY = """
WITH days AS (
    SELECT day::date
    FROM generate_series(CAST(:first_day AS date), CAST(:last_day AS date), interval '1 day')
        AS day
)
"""

DAILY_FUNNEL = (
    DAILY
    + """,
activity AS (
    SELECT (occurred_at AT TIME ZONE 'UTC')::date AS day,
           count(*) FILTER (WHERE action = 'lead.collected') AS collected,
           count(*) FILTER (WHERE action = 'approval.approved') AS approved,
           count(*) FILTER (WHERE action = 'approval.rejected') AS rejected,
           count(*) FILTER (WHERE action = 'lead.routed' AND details ->> 'route' = 'escalate')
               AS escalated
    FROM audit_log
    WHERE occurred_at >= :since
      AND action IN ('lead.collected', 'approval.approved', 'approval.rejected', 'lead.routed')
    GROUP BY 1
)
SELECT days.day,
       coalesce(activity.collected, 0) AS collected,
       coalesce(activity.approved, 0) AS approved,
       coalesce(activity.rejected, 0) AS rejected,
       coalesce(activity.escalated, 0) AS escalated
FROM days
LEFT JOIN activity USING (day)
ORDER BY days.day
"""
)

APPROVED_RUNNING_TOTAL = (
    DAILY
    + """,
before AS (
    SELECT count(*) AS approved
    FROM approvals
    WHERE decision = 'approved' AND created_at < :since
),
per_day AS (
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS approved
    FROM approvals
    WHERE decision = 'approved' AND created_at >= :since
    GROUP BY 1
)
SELECT days.day,
       coalesce(per_day.approved, 0) AS approved,
       (SELECT approved FROM before)
           + sum(coalesce(per_day.approved, 0)) OVER (ORDER BY days.day)::int AS running_total
FROM days
LEFT JOIN per_day USING (day)
ORDER BY days.day
"""
)

TOP_LEADS_PER_AREA = """
SELECT city, area, rank, lead_id, name, score, rating, review_count, status
FROM (
    SELECT city, area, id AS lead_id, name, score, rating, review_count, status,
           RANK() OVER (
               PARTITION BY city, area
               ORDER BY score DESC, review_count DESC NULLS LAST, rating DESC NULLS LAST
           ) AS rank
    FROM leads
    WHERE score IS NOT NULL AND area IS NOT NULL AND NOT opt_out AND status <> 'rejected'
) AS ranked
WHERE rank <= :top
ORDER BY city, area, rank, lead_id
"""

REVIEWER_STATS = """
SELECT users.email AS reviewer,
       count(*) AS decisions,
       count(*) FILTER (WHERE approvals.decision = 'approved') AS approved,
       count(*) FILTER (WHERE approvals.decision = 'rejected') AS rejected,
       round(100.0 * count(*) FILTER (WHERE approvals.decision = 'approved') / count(*), 1)::float8
           AS approved_pct,
       round(
           (percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM approvals.created_at - gate.waiting_since)
           ) / 60)::numeric,
           1
       )::float8 AS median_wait_minutes
FROM approvals
JOIN users ON users.id = approvals.reviewer_id
-- When each item reached the gate. One grouped pass over the steps, joined by hash: a
-- per-approval look-up (LATERAL) took minutes without an index (docs/sql-notes.md).
LEFT JOIN (
    SELECT run_item_id, min(started_at) AS waiting_since
    FROM run_steps
    WHERE node = 'approval_gate'
    GROUP BY run_item_id
) AS gate ON gate.run_item_id = approvals.run_item_id
GROUP BY users.email
ORDER BY decisions DESC, reviewer
"""

NODE_DURATIONS = """
SELECT node,
       count(*) FILTER (WHERE status = 'completed') AS completed,
       count(*) FILTER (WHERE status = 'failed') AS failed,
       round(avg(duration_ms) FILTER (WHERE status = 'completed'))::int AS avg_ms,
       round(
           percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
               FILTER (WHERE status = 'completed')
       )::int AS p95_ms
FROM run_steps
WHERE duration_ms IS NOT NULL
GROUP BY node
ORDER BY array_position(
    ARRAY['enrich', 'extract', 'check', 'score', 'approval_gate', 'route', 'act'], node
), node
"""

SCORE_DISTRIBUTION = """
WITH buckets (bucket, low) AS (
    VALUES ('0-19', 0), ('20-39', 20), ('40-59', 40), ('60-79', 60), ('80-100', 80)
),
scored AS (
    SELECT CASE
               WHEN score >= 80 THEN '80-100'
               WHEN score >= 60 THEN '60-79'
               WHEN score >= 40 THEN '40-59'
               WHEN score >= 20 THEN '20-39'
               ELSE '0-19'
           END AS bucket,
           status
    FROM leads
    WHERE score IS NOT NULL
)
SELECT buckets.bucket,
       count(scored.bucket) AS leads,
       count(*) FILTER (WHERE scored.status = 'approved') AS approved,
       count(*) FILTER (WHERE scored.status = 'rejected') AS rejected
FROM buckets
LEFT JOIN scored USING (bucket)
GROUP BY buckets.bucket, buckets.low
ORDER BY buckets.low
"""

CAMPAIGN_CONVERSION = """
WITH per_lead AS (
    SELECT runs.campaign_id,
           run_items.lead_id,
           bool_or(approvals.decision = 'approved') AS approved,
           bool_or(EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(run_items.flags) AS flag
               WHERE flag LIKE 'possible\\_duplicate:%'
           )) AS duplicate
    FROM run_items
    JOIN runs ON runs.id = run_items.run_id
    LEFT JOIN approvals ON approvals.run_item_id = run_items.id
    GROUP BY runs.campaign_id, run_items.lead_id
)
SELECT campaigns.id AS campaign_id,
       campaigns.name,
       count(per_lead.lead_id) AS collected,
       count(*) FILTER (WHERE per_lead.approved) AS approved,
       round(100.0 * count(*) FILTER (WHERE per_lead.approved)
             / nullif(count(per_lead.lead_id), 0), 1)::float8 AS approved_pct,
       round(100.0 * count(*) FILTER (WHERE leads.email IS NOT NULL)
             / nullif(count(per_lead.lead_id), 0), 1)::float8 AS emails_found_pct,
       round(100.0 * count(*) FILTER (WHERE per_lead.duplicate)
             / nullif(count(per_lead.lead_id), 0), 1)::float8 AS duplicates_pct
FROM campaigns
LEFT JOIN per_lead ON per_lead.campaign_id = campaigns.id
LEFT JOIN leads ON leads.id = per_lead.lead_id
GROUP BY campaigns.id, campaigns.name
ORDER BY campaigns.id
"""


class ReportName(StrEnum):
    DAILY_FUNNEL = "daily_funnel"
    APPROVED_RUNNING_TOTAL = "approved_running_total"
    TOP_LEADS_PER_AREA = "top_leads_per_area"
    REVIEWER_STATS = "reviewer_stats"
    NODE_DURATIONS = "node_durations"
    SCORE_DISTRIBUTION = "score_distribution"
    CAMPAIGN_CONVERSION = "campaign_conversion"


@dataclass(frozen=True)
class Report:
    name: ReportName
    description: str
    sql: str
    # The query parameters it takes (`days`, `top`); the API checks their ranges.
    params: tuple[str, ...] = field(default=())


REPORTS = {
    report.name: report
    for report in [
        Report(
            ReportName.DAILY_FUNNEL,
            "Per UTC day: leads collected, approved, rejected and escalated (last `days` days).",
            DAILY_FUNNEL,
            ("days",),
        ),
        Report(
            ReportName.APPROVED_RUNNING_TOTAL,
            "Per UTC day: leads approved that day and the running total of all approvals.",
            APPROVED_RUNNING_TOTAL,
            ("days",),
        ),
        Report(
            ReportName.TOP_LEADS_PER_AREA,
            "The best-scored leads in each city + area (rank ≤ `top`; ties share a rank; "
            "rejected and opted-out leads left out).",
            TOP_LEADS_PER_AREA,
            ("top",),
        ),
        Report(
            ReportName.REVIEWER_STATS,
            "Per reviewer: decisions, approved, rejected, approval % and the median wait "
            "between a lead reaching the gate and the decision.",
            REVIEWER_STATS,
        ),
        Report(
            ReportName.NODE_DURATIONS,
            "Per graph node: steps completed and failed, average and p95 time in the node "
            "(the human's wait isn't in it: see reviewer_stats).",
            NODE_DURATIONS,
        ),
        Report(
            ReportName.SCORE_DISTRIBUTION,
            "Scored leads per score bucket, with how many were approved and rejected.",
            SCORE_DISTRIBUTION,
        ),
        Report(
            ReportName.CAMPAIGN_CONVERSION,
            "Per campaign: leads collected → approved, and the % with an email and the % "
            "flagged as a possible duplicate.",
            CAMPAIGN_CONVERSION,
        ),
    ]
}


def report_params(report: Report, today: date, days: int, top: int) -> dict[str, Any]:
    """The bind values the report's SQL needs.

    `days` becomes the first and last day, and `since`: the first day's midnight, UTC.
    """
    params: dict[str, Any] = {}
    if "days" in report.params:
        first_day = today - timedelta(days=days - 1)
        since = datetime.combine(first_day, time(), UTC)
        params |= {"first_day": first_day, "last_day": today, "since": since}
    if "top" in report.params:
        params["top"] = top
    return params


def run_report(
    session: Session, name: ReportName, today: date, days: int = 30, top: int = 3
) -> list[dict[str, Any]]:
    """The report's rows as dicts (column name -> value)."""
    report = REPORTS[name]
    result = session.execute(text(report.sql), report_params(report, today, days, top))
    return [dict(row._mapping) for row in result]
