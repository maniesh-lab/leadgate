"""Tests for the score explanation's prompt and checks (`app.agents.nodes.score`)."""

from app.agents.nodes.score import Explanation, build_prompt, explanation_validators
from app.agents.scoring.rubric import score_lead
from app.db.seed import WEB_AGENCY_RULES

OLD_SITE = {
    "website": "https://old.test",
    "category_matches_niche": True,
    "signals": {"copyright_year": 2011, "https_and_mobile": True},
}


def test_prompt_lists_matched_and_missed_rules_apart() -> None:
    prompt = build_prompt(score_lead(WEB_AGENCY_RULES, OLD_SITE, 2026))

    matched, missed = prompt.split("Rules the lead did NOT match:")
    assert "copyright year older than 3 years (+20)" in matched
    assert "no website (worth 30)" in missed
    assert "no website" not in matched


def test_prompt_says_why_a_gate_made_it_zero() -> None:
    result = score_lead(WEB_AGENCY_RULES, OLD_SITE | {"category_matches_niche": False}, 2026)
    assert "required rule failed: category matches niche" in build_prompt(result)


def test_explanation_checks() -> None:
    checks = explanation_validators(score_lead(WEB_AGENCY_RULES, OLD_SITE, 2026))

    def problems(text: str) -> list[str]:
        return [p for check in checks if (p := check(Explanation(text=text)))]

    assert problems("Scores 20 of 100: the site's copyright is over 3 years old.") == []
    assert "not in the rules: 55" in problems("Scores 55.")[0]
    assert problems(" ") == ["text is empty"]
    assert "under 400" in problems("x" * 401)[0]
