"""The rubric engine: a campaign's scoring rules (plain data) → a 0–100 score.

A rubric is a list of rules like
`{"label": "has email", "field": "email", "op": "exists", "value": None, "points": 20}`
(the two seeded rubrics are in `app/db/seed.py`). Each rule looks up one fact about
the lead and either hits (its points count) or misses. The score is the sum, capped
at 100. A rule with `"required": True` is a gate: if it misses, the score is 0 no
matter what else hit.

Facts are a plain dict built by the `score` node: the lead's extracted fields,
`business_status`, `category_matches_niche`, and `signals` (the website signals).
A field with a dot reads inside a dict: `signals.copyright_year`.

An unknown fact (None) never hits a rule, except `missing`. So a site whose
copyright year we couldn't find doesn't count as "old".

No LLM here: the score is pure arithmetic, so the same lead always gets the same
score. The `score` node asks the LLM only to explain the breakdown in words.
"""

from dataclasses import dataclass
from typing import Any

MAX_SCORE = 100
OPS = ("exists", "missing", "eq", "gte", "lte", "in", "contains", "older_than_years")


@dataclass
class RubricResult:
    score: int
    # One entry per rule: {"rule": label, "field", "points", "hit", "required"}
    breakdown: list[dict[str, Any]]
    # Labels of the required rules that missed (each one sets the score to 0).
    failed_gates: list[str]


def score_lead(rules: list[dict[str, Any]], facts: dict[str, Any], year: int) -> RubricResult:
    """Score the facts with the rules. `year` is the current year (for `older_than_years`)."""
    breakdown = []
    failed_gates = []
    total = 0
    for rule in rules:
        hit = rule_hits(rule, lookup(facts, rule["field"]), year)
        required = bool(rule.get("required", False))
        breakdown.append(
            {
                "rule": rule["label"],
                "field": rule["field"],
                "points": rule["points"],
                "hit": hit,
                "required": required,
            }
        )
        if hit:
            total += rule["points"]
        elif required:
            failed_gates.append(rule["label"])

    score = 0 if failed_gates else max(0, min(MAX_SCORE, total))
    return RubricResult(score=score, breakdown=breakdown, failed_gates=failed_gates)


def lookup(facts: dict[str, Any], field: str) -> Any:
    """Read `field` from the facts; "signals.https" reads facts["signals"]["https"]."""
    value: Any = facts
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def rule_hits(rule: dict[str, Any], value: Any, year: int) -> bool:
    """True when the rule's condition holds for `value`. Raises ValueError for an unknown op."""
    op = rule["op"]
    target = rule.get("value")
    if op == "exists":
        return not is_empty(value)
    if op == "missing":
        return is_empty(value)
    if value is None:
        return False
    if op == "eq":
        return value == target
    if op == "gte":
        return value >= target
    if op == "lte":
        return value <= target
    if op == "in":
        return value in target
    if op == "contains":
        if isinstance(value, str):
            return str(target).casefold() in value.casefold()
        return target in value
    if op == "older_than_years":
        return year - int(value) > target
    raise ValueError(f"unknown rubric op {op!r} (known: {', '.join(OPS)})")
