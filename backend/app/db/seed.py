"""Starter data: the first admin user and the two scoring rubrics from PLAN §5.

`python -m app.cli seed` calls `seed()`. It is safe to run again: rows that
already exist are left alone (an existing admin keeps its password).

Rubric rules read a field from the lead being scored. Phase 5's rubric engine
looks fields up like this:
- `phone`, `email`, `website`, `rating`, `review_count`, `business_status`: lead fields
- `category_matches_niche`: set by the `check` node
- `signals.<name>`: the site signals Phase 4 stores in `lead.enrichment["signals"]`
A rule with `"required": True` is a gate: if it misses, the score is 0 and the lead is flagged.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.db.models import ScoringRubric, User, UserRole


def rule(label: str, field: str, op: str, value: Any, points: int, **extra: Any) -> dict[str, Any]:
    return {"label": label, "field": field, "op": op, "value": value, "points": points, **extra}


GENERAL_B2B_RULES = [
    rule("has phone", "phone", "exists", None, 15),
    rule("has email", "email", "exists", None, 20),
    rule("has website", "website", "exists", None, 10),
    rule("rating 4.0 or more", "rating", "gte", 4.0, 15),
    rule("10 or more reviews", "review_count", "gte", 10, 15),
    rule("business operational", "business_status", "eq", "operational", 10),
    rule("category matches niche", "category_matches_niche", "eq", True, 15),
]

# The dentist / web-design-agency example: good reviews + an old or missing website = hot lead.
WEB_AGENCY_RULES = [
    rule("category matches niche", "category_matches_niche", "eq", True, 0, required=True),
    rule("rating 4.0 or more", "rating", "gte", 4.0, 20),
    rule("20 or more reviews", "review_count", "gte", 20, 15),
    rule("no website", "website", "missing", None, 30),
    rule("copyright year older than 3 years", "signals.copyright_year", "older_than_years", 3, 20),
    # The rule ops have no "or", so Phase 4 stores one signal that is true only with both.
    rule("no https or no mobile viewport", "signals.https_and_mobile", "eq", False, 15),
]

RUBRICS = {"general-b2b": GENERAL_B2B_RULES, "web-agency": WEB_AGENCY_RULES}


@dataclass
class SeedResult:
    admin_created: bool
    rubrics_created: list[str]


def seed(session: Session, admin_email: str, admin_password: str) -> SeedResult:
    admin = session.scalars(select(User).where(User.email == admin_email)).one_or_none()
    admin_created = admin is None
    if admin is None:
        admin = User(
            email=admin_email, password_hash=hash_password(admin_password), role=UserRole.ADMIN
        )
        session.add(admin)
        session.flush()  # gives admin an id for created_by below

    rubrics_created = []
    for name, rules in RUBRICS.items():
        exists = session.scalars(
            select(ScoringRubric).where(ScoringRubric.name == name, ScoringRubric.version == 1)
        ).one_or_none()
        if exists is None:
            session.add(ScoringRubric(name=name, version=1, rules=rules, created_by=admin.id))
            rubrics_created.append(f"{name} v1")

    session.commit()
    return SeedResult(admin_created=admin_created, rubrics_created=rubrics_created)
