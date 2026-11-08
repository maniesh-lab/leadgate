"""Tests for `/api/rubrics`: list, read, create (v1) and edit (a new version), and the
rubrics' plain-words descriptions."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ScoringRubric
from app.db.seed import RUBRIC_DESCRIPTIONS, WEB_AGENCY_RULES, seed

RULES = [
    {"label": "has email", "field": "email", "op": "exists", "points": 40},
    {"label": "good rating", "field": "rating", "op": "gte", "value": 4.0, "points": 60},
]


def test_admin_creates_then_edits_a_rubric(client: TestClient, db: Session, as_admin) -> None:
    created = client.post(
        "/api/rubrics", json={"name": "my-rules", "rules": RULES}, headers=as_admin
    )
    assert created.status_code == 201
    v1 = created.json()
    assert (v1["name"], v1["version"]) == ("my-rules", 1)
    assert v1["rules"][0] == {
        "label": "has email",
        "field": "email",
        "op": "exists",
        "value": None,
        "points": 40,
        "required": False,
    }

    edited = client.post(f"/api/rubrics/{v1['id']}", json={"rules": RULES[:1]}, headers=as_admin)
    assert edited.status_code == 201
    v2 = edited.json()
    assert (v2["name"], v2["version"], len(v2["rules"])) == ("my-rules", 2, 1)
    # Editing the old version again still makes the next version.
    again = client.post(f"/api/rubrics/{v1['id']}", json={"rules": RULES}, headers=as_admin)
    assert again.json()["version"] == 3
    # v1 is untouched.
    assert len(db.get(ScoringRubric, v1["id"]).rules) == 2


def test_a_description_carries_over_to_the_next_version(client: TestClient, as_admin) -> None:
    body = {"name": "my-rules", "rules": RULES, "description": "  For finding new clinics.  "}
    v1 = client.post("/api/rubrics", json=body, headers=as_admin).json()
    assert v1["description"] == "For finding new clinics."

    def edit(**extra) -> str | None:
        response = client.post(
            f"/api/rubrics/{v1['id']}", json={"rules": RULES, **extra}, headers=as_admin
        )
        return response.json()["description"]

    assert edit() == "For finding new clinics."  # kept
    assert edit(description="For dentists.") == "For dentists."
    assert edit(description=" ") is None  # blank clears it
    too_long = {"name": "long", "rules": RULES, "description": "x" * 301}
    assert client.post("/api/rubrics", json=too_long, headers=as_admin).status_code == 422


def test_seeding_gives_both_rubrics_a_description(db: Session) -> None:
    seed(db, "admin@example.com", "a-long-enough-password")
    rubrics = db.scalars(select(ScoringRubric)).all()
    assert {r.name: r.description for r in rubrics} == RUBRIC_DESCRIPTIONS


def test_seeded_rules_are_valid_rules(client: TestClient, as_admin) -> None:
    body = {"name": "web-agency-copy", "rules": WEB_AGENCY_RULES}
    assert client.post("/api/rubrics", json=body, headers=as_admin).status_code == 201


def test_taken_name_is_a_conflict(client: TestClient, as_admin) -> None:
    client.post("/api/rubrics", json={"name": "dup", "rules": RULES}, headers=as_admin)
    again = client.post("/api/rubrics", json={"name": "dup", "rules": RULES}, headers=as_admin)
    assert again.status_code == 409


def test_broken_rules_are_refused(client: TestClient, as_admin) -> None:
    broken = [
        {"label": "x", "field": "rating", "op": "gte", "value": "four", "points": 10},
        {"label": "x", "field": "category", "op": "in", "value": "dentist", "points": 10},
        {"label": "x", "field": "email", "op": "eq", "points": 10},
        {"label": "x", "field": "email", "op": "matches", "value": 1, "points": 10},
        {"label": "x", "field": "Email!", "op": "exists", "points": 10},
        {"label": "x", "field": "email", "op": "exists", "points": 500},
    ]
    for rule in broken:
        body = {"name": "bad", "rules": [rule]}
        response = client.post("/api/rubrics", json=body, headers=as_admin)
        assert response.status_code == 422, rule
    no_rules = client.post("/api/rubrics", json={"name": "bad", "rules": []}, headers=as_admin)
    assert no_rules.status_code == 422


def test_reviewers_read_but_dont_write(client: TestClient, as_admin, as_reviewer) -> None:
    v1 = client.post("/api/rubrics", json={"name": "r", "rules": RULES}, headers=as_admin).json()
    client.post("/api/rubrics", json={"name": "other", "rules": RULES}, headers=as_admin)
    client.post(f"/api/rubrics/{v1['id']}", json={"rules": RULES}, headers=as_admin)

    listed = client.get("/api/rubrics", headers=as_reviewer).json()
    assert [(r["name"], r["version"]) for r in listed["items"]] == [
        ("other", 1),
        ("r", 2),
        ("r", 1),
    ]
    only_r = client.get("/api/rubrics?name=r", headers=as_reviewer).json()
    assert only_r["total"] == 2
    assert client.get(f"/api/rubrics/{v1['id']}", headers=as_reviewer).json()["name"] == "r"

    body = {"name": "nope", "rules": RULES}
    assert client.post("/api/rubrics", json=body, headers=as_reviewer).status_code == 403
    edit = client.post(f"/api/rubrics/{v1['id']}", json={"rules": RULES}, headers=as_reviewer)
    assert edit.status_code == 403


def test_unknown_rubric_is_404(client: TestClient, as_admin) -> None:
    assert client.get("/api/rubrics/99", headers=as_admin).status_code == 404
    assert (
        client.post("/api/rubrics/99", json={"rules": RULES}, headers=as_admin).status_code == 404
    )
