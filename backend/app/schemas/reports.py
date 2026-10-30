"""Bodies for `/api/reports`: the list of reports and one report's rows."""

from typing import Any

from pydantic import BaseModel

from app.reporting.queries import ReportName


class ReportInfo(BaseModel):
    name: ReportName
    description: str
    # The query parameters it takes: "days" (1–365) and/or "top" (1–20).
    params: list[str]


class ReportOut(BaseModel):
    name: ReportName
    description: str
    # The parameter values used, e.g. {"days": 30}.
    params: dict[str, int]
    # One dict per row: column name -> value (numbers, strings, ISO dates).
    rows: list[dict[str, Any]]
