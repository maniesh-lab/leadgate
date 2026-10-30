"""Tests for the rubric engine (`app.agents.scoring.rubric`), on both seeded rubrics."""

import pytest

from app.agents.scoring.rubric import lookup, rule_hits, score_lead
from app.db.seed import GENERAL_B2B_RULES, WEB_AGENCY_RULES

YEAR = 2026


def facts(**overrides):
    """A lead that hits every general-b2b rule; override fields to miss some."""
    base = {
        "phone": "+15127077300",
        "email": "info@aloha-dental.test",
        "website": "https://aloha-dental.test",
        "rating": 4.6,
        "review_count": 120,
        "business_status": "operational",
        "category_matches_niche": True,
        "signals": {"copyright_year": 2025, "https_and_mobile": True},
    }
    return base | overrides


def hits(result) -> dict[str, bool]:
    return {entry["rule"]: entry["hit"] for entry in result.breakdown}


def test_general_b2b_full_marks() -> None:
    result = score_lead(GENERAL_B2B_RULES, facts(), YEAR)
    assert result.score == 100
    assert result.failed_gates == []


def test_general_b2b_osm_lead_without_reviews_or_email() -> None:
    # OSM has no ratings, reviews or status; this one has no email either.
    lead = facts(email=None, rating=None, review_count=None, business_status=None)

    result = score_lead(GENERAL_B2B_RULES, lead, YEAR)

    assert result.score == 15 + 10 + 15  # phone + website + category
    assert hits(result)["has email"] is False
    assert hits(result)["rating 4.0 or more"] is False


def test_breakdown_lists_every_rule_with_its_points() -> None:
    result = score_lead(GENERAL_B2B_RULES, facts(), YEAR)
    assert result.breakdown[0] == {
        "rule": "has phone",
        "field": "phone",
        "points": 15,
        "hit": True,
        "required": False,
    }
    assert len(result.breakdown) == len(GENERAL_B2B_RULES)


def test_web_agency_hot_lead_has_no_website() -> None:
    lead = facts(website=None, signals={"copyright_year": None, "https_and_mobile": None})

    result = score_lead(WEB_AGENCY_RULES, lead, YEAR)

    assert result.score == 20 + 15 + 30  # good rating + many reviews + no website
    assert hits(result)["copyright year older than 3 years"] is False  # unknown is not old


def test_web_agency_old_site_without_https() -> None:
    lead = facts(signals={"copyright_year": 2011, "https_and_mobile": False})

    result = score_lead(WEB_AGENCY_RULES, lead, YEAR)

    assert result.score == 20 + 15 + 20 + 15
    assert hits(result)["no website"] is False


def test_web_agency_modern_site_scores_only_reviews() -> None:
    assert score_lead(WEB_AGENCY_RULES, facts(), YEAR).score == 35


def test_web_agency_category_gate_sets_the_score_to_zero() -> None:
    lead = facts(website=None, category_matches_niche=False)

    result = score_lead(WEB_AGENCY_RULES, lead, YEAR)

    assert result.score == 0
    assert result.failed_gates == ["category matches niche"]


def test_score_is_capped_at_100() -> None:
    rules = [{"label": f"r{n}", "field": "phone", "op": "exists", "points": 60} for n in range(3)]
    assert score_lead(rules, facts(), YEAR).score == 100


def test_lookup_reads_dotted_fields() -> None:
    assert lookup(facts(), "signals.copyright_year") == 2025
    assert lookup(facts(), "signals.nothing") is None
    assert lookup(facts(signals=None), "signals.copyright_year") is None


@pytest.mark.parametrize(
    ("op", "target", "value", "expected"),
    [
        ("exists", None, "x", True),
        ("exists", None, [], False),
        ("missing", None, None, True),
        ("missing", None, "", True),
        ("eq", "operational", "operational", True),
        ("gte", 4.0, 4.0, True),
        ("gte", 4.0, None, False),
        ("lte", 3, 4, False),
        ("in", ["dentist", "doctor"], "doctor", True),
        ("contains", "implant", "Dental Implants", True),
        ("contains", "implants", ["implants", "crowns"], True),
        ("older_than_years", 3, 2022, True),
        ("older_than_years", 3, 2023, False),
        ("older_than_years", 3, None, False),
    ],
)
def test_rule_ops(op, target, value, expected) -> None:
    assert rule_hits({"op": op, "value": target}, value, YEAR) is expected


def test_unknown_op_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown rubric op"):
        rule_hits({"op": "near", "value": 1}, 2, YEAR)
