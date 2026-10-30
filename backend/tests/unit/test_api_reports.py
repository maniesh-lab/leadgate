"""Tests for `/api/reports` on SQLite: the list, checks and the "needs Postgres" answer.

The reports' SQL itself is Postgres-only; it's tested in
`tests/integration/test_reports_postgres.py`.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.reporting.queries import REPORTS


def test_list_reports(client: TestClient, db: Session, as_reviewer) -> None:
    response = client.get("/api/reports", headers=as_reviewer)
    assert response.status_code == 200
    body = response.json()
    assert [report["name"] for report in body] == list(REPORTS)
    assert all(report["description"] for report in body)
    funnel = next(report for report in body if report["name"] == "daily_funnel")
    assert funnel["params"] == ["days"]


def test_reports_need_a_login(client: TestClient, db: Session) -> None:
    assert client.get("/api/reports").status_code == 401
    assert client.get("/api/reports/daily_funnel").status_code == 401


def test_a_report_on_sqlite_says_it_needs_postgres(
    client: TestClient, db: Session, as_reviewer
) -> None:
    response = client.get("/api/reports/daily_funnel", headers=as_reviewer)
    assert response.status_code == 501
    assert response.json()["error"] == {
        "code": "not_implemented",
        "message": "reports need Postgres",
        "details": None,
    }


def test_unknown_report_and_bad_params(client: TestClient, db: Session, as_reviewer) -> None:
    assert client.get("/api/reports/nope", headers=as_reviewer).status_code == 422
    for query in ("days=0", "days=366", "top=0", "top=21"):
        response = client.get(f"/api/reports/daily_funnel?{query}", headers=as_reviewer)
        assert response.status_code == 422, query
        assert response.json()["error"]["code"] == "validation_error"
