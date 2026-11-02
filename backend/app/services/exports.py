"""CSV export: a run's or a campaign's approved leads, ready for a spreadsheet or a CRM.

Called by `GET /api/exports/approved-leads` (`app.api.routes.exports`).

- "Approved" = a reviewer approved the run item, escalated ones included; the `route`
  column says which (notify = sent to the CRM, escalate = an admin looks first).
- Opted-out leads are always left out (their contact data was wiped anyway).
- A campaign's export has one row per lead: the newest approval, if the lead was
  approved in several runs.
- A cell that starts like a spreadsheet formula (`=`, `@`, `+cmd`, ...) gets a leading `'`,
  so a value copied from a website can't run as a formula in Excel. Phone numbers such as
  `+15125550100` are left as they are.
"""

import csv
import io
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Approval, Decision, Lead, Run, RunItem, User

COLUMNS = (
    "lead_id",
    "name",
    "phone",
    "email",
    "website",
    "address",
    "area",
    "city",
    "country",
    "category",
    "rating",
    "review_count",
    "score",
    "route",
    "flags",
    "run_id",
    "run_item_id",
    "approved_at",
    "approved_by",
)
# OWASP's "CSV injection" list: a cell starting with one of these may run as a formula.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# ...but a plain signed number (a phone number) can't, so it is left alone.
PLAIN_NUMBER = re.compile(r"^[+-][\d\s().-]*$")


def approved_leads_csv(
    session: Session, *, run_id: int | None = None, campaign_id: int | None = None
) -> tuple[str, int]:
    """The CSV text (with Excel's UTF-8 marker) and how many leads it holds."""
    stmt = (
        select(RunItem, Lead, Approval, User.email)
        .join(Lead, Lead.id == RunItem.lead_id)
        .join(Approval, Approval.run_item_id == RunItem.id)
        .join(User, User.id == Approval.reviewer_id)
        .where(Approval.decision == Decision.APPROVED, Lead.opt_out.is_(False))
        .order_by(Approval.created_at.desc(), Approval.id.desc())
    )
    if run_id is not None:
        stmt = stmt.where(RunItem.run_id == run_id)
    if campaign_id is not None:
        stmt = stmt.join(Run, Run.id == RunItem.run_id).where(Run.campaign_id == campaign_id)

    rows: dict[int, list[Any]] = {}  # lead id -> row; the newest approval wins
    for item, lead, approval, reviewer in session.execute(stmt):
        if lead.id not in rows:
            rows[lead.id] = row_for(item, lead, approval, reviewer)

    out = io.StringIO()
    out.write("﻿")  # Excel reads the file as UTF-8 only with this marker
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    for row in sorted(rows.values(), key=lambda row: row[0]):
        writer.writerow([safe_cell(value) for value in row])
    return out.getvalue(), len(rows)


def row_for(item: RunItem, lead: Lead, approval: Approval, reviewer: str) -> list[Any]:
    return [
        lead.id,
        lead.name,
        lead.phone,
        lead.email,
        lead.website,
        lead.address,
        lead.area,
        lead.city,
        lead.country,
        lead.category,
        lead.rating,
        lead.review_count,
        item.score,
        item.route.value if item.route else None,
        " ".join(item.flags or []),
        item.run_id,
        item.id,
        approval.created_at.isoformat(),
        reviewer,
    ]


def safe_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return "" if value is None else value
    if value.startswith(FORMULA_START) and not PLAIN_NUMBER.match(value):
        return "'" + value
    return value
