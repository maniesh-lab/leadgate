"""Reports for the dashboard: `GET /api/reports` (what exists) and `GET /api/reports/{name}`.

The SQL lives in `app.reporting.queries`. It is Postgres-only (window functions,
`percentile_cont`, JSONB), so on any other database the answer is 501.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.api.errors import error_docs
from app.reporting.queries import REPORTS, ReportName, run_report
from app.schemas.reports import ReportInfo, ReportOut

router = APIRouter(prefix="/reports", tags=["reports"], responses=error_docs(401))


@router.get("")
def list_reports(_user: CurrentUser) -> list[ReportInfo]:
    """Every report, with what it shows and the parameters it takes."""
    return [
        ReportInfo(name=report.name, description=report.description, params=list(report.params))
        for report in REPORTS.values()
    ]


@router.get("/{name}", responses=error_docs(501))
def get_report(
    name: ReportName,
    _user: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=365, description="daily reports: how many days")] = 30,
    top: Annotated[int, Query(ge=1, le=20, description="top_leads_per_area: ranks kept")] = 3,
) -> ReportOut:
    """One report's rows (see `GET /api/reports` for what each one shows)."""
    if db.get_bind().dialect.name != "postgresql":
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "reports need Postgres")
    report = REPORTS[name]
    given = {"days": days, "top": top}
    rows = run_report(db, name, datetime.now(UTC).date(), days=days, top=top)
    return ReportOut(
        name=report.name,
        description=report.description,
        params={param: given[param] for param in report.params},
        rows=rows,
    )
