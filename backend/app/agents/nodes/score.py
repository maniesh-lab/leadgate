"""Node 4, `score`: the run's rubric gives the lead 0–100; the LLM explains it in words.

The score is pure arithmetic (`app.agents.scoring.rubric`), so it never depends on
the LLM. The LLM only writes one or two sentences from the breakdown; a check makes
sure it uses no number that isn't in the breakdown. If the LLM fails, a plain
template sentence is used (that template is also the answer in LLM_MODE=fake).

A required rule that misses (web-agency's "category matches niche") sets the score
to 0 and adds a `rubric_gate:<rule>` flag. No side effects.
"""

import re

import structlog
from langgraph.runtime import Runtime
from pydantic import BaseModel

from app.agents.llm import LLMError, Validator
from app.agents.scoring.rubric import RubricResult, score_lead
from app.agents.state import AgentContext, LeadState
from app.db.models import ScoringRubric

log = structlog.get_logger(__name__)

MAX_EXPLANATION_CHARS = 400
NUMBER = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = (
    "You explain a sales lead's score to a busy salesperson in one or two plain "
    "sentences: which rules the lead MATCHED, and the most valuable rules it did NOT "
    "match. A rule's name describes the rule, not the business: only a matched rule is "
    'true about the business. Use only the rules listed. Answer as JSON: {"text": "..."}'
)


class Explanation(BaseModel):
    text: str


def score(state: LeadState, runtime: Runtime[AgentContext]) -> LeadState:
    context = runtime.context
    rubric = context.session.get(ScoringRubric, state["rubric_id"])
    if rubric is None:
        raise LookupError(f"rubric {state['rubric_id']} not found")

    result = score_lead(rubric.rules, scoring_facts(state), context.now().year)
    try:
        explanation = context.llm.structured(
            system=SYSTEM_PROMPT,
            prompt=build_prompt(result),
            schema=Explanation,
            fake_answer=lambda: Explanation(text=template_explanation(result)),
            validators=explanation_validators(result),
        ).text
    except LLMError:
        explanation = template_explanation(result)

    flags = [*state.get("flags", []), *(f"rubric_gate:{label}" for label in result.failed_gates)]
    log.info("node_score", lead_id=state["lead_id"], score=result.score)
    return {
        "score": result.score,
        "score_breakdown": result.breakdown,
        "score_explanation": explanation,
        "flags": flags,
    }


def scoring_facts(state: LeadState) -> dict:
    """What the rubric rules can read: the extracted lead + status, category match, signals."""
    return {
        **state["extracted"],
        "business_status": state["cleaned"].get("business_status"),
        "category_matches_niche": state.get("category_matches_niche", False),
        "signals": state.get("enrichment", {}).get("signals") or {},
    }


def build_prompt(result: RubricResult) -> str:
    # Two separate lists: a "rule: no" line was misread as a fact ("no website: no" became
    # "has no website") in the live check.
    matched = [
        f"- {entry['rule']} (+{entry['points']})" for entry in result.breakdown if entry["hit"]
    ]
    missed = [
        f"- {entry['rule']} (worth {entry['points']})"
        for entry in result.breakdown
        if not entry["hit"] and not entry["required"]
    ]
    lines = [f"Score: {result.score} out of 100."]
    if result.failed_gates:
        lines.append(
            f"The score is 0 because a required rule failed: {', '.join(result.failed_gates)}."
        )
    lines += ["Rules the lead MATCHED:", *(matched or ["- (none)"])]
    lines += ["Rules the lead did NOT match:", *(missed or ["- (none)"])]
    return "\n".join(lines)


def template_explanation(result: RubricResult) -> str:
    if result.failed_gates:
        return f"Score 0/100: required rule failed ({', '.join(result.failed_gates)})."
    hits = [
        f"{entry['rule']} (+{entry['points']})"
        for entry in result.breakdown
        if entry["hit"] and entry["points"] > 0
    ]
    if not hits:
        return f"Score {result.score}/100: no rule matched."
    return f"Score {result.score}/100: {', '.join(hits)}."


def explanation_validators(result: RubricResult) -> list[Validator]:
    """The explanation must be short and may only use numbers from the breakdown."""
    allowed = {float(result.score), 100.0}
    for entry in result.breakdown:
        allowed.add(float(entry["points"]))
        allowed.update(float(number) for number in NUMBER.findall(entry["rule"]))

    def short_and_not_empty(answer: Explanation) -> str | None:
        if not answer.text.strip():
            return "text is empty"
        if len(answer.text) > MAX_EXPLANATION_CHARS:
            return f"text must be under {MAX_EXPLANATION_CHARS} characters"
        return None

    def only_known_numbers(answer: Explanation) -> str | None:
        unknown = [n for n in NUMBER.findall(answer.text) if float(n) not in allowed]
        if not unknown:
            return None
        return f"text uses numbers that are not in the rules: {', '.join(unknown)}"

    return [short_and_not_empty, only_known_numbers]
