"""Tests for the LLM wrapper (`app.agents.llm`): fake mode, the retry loop, picking the mode.

No network: the retry loop runs on `ScriptedProvider`, which replays canned replies.
"""

import pytest
import structlog
from pydantic import BaseModel

from app.agents.llm import (
    FakeLLM,
    LLMError,
    SteadfastLLM,
    TokenBudget,
    guess_tokens,
    make_llm,
    scripted_llm,
)
from app.config import Settings


class Pet(BaseModel):
    name: str
    legs: int


def four_legs(pet: Pet) -> str | None:
    return None if pet.legs == 4 else "legs must be 4"


def ask(llm, validators=(four_legs,)) -> Pet:
    return llm.structured(
        system="Answer in JSON.",
        prompt="Describe a dog.",
        schema=Pet,
        fake_answer=lambda: Pet(name="Rex", legs=4),
        validators=validators,
    )


def test_fake_llm_returns_the_fake_answer() -> None:
    llm = FakeLLM()
    assert ask(llm) == Pet(name="Rex", legs=4)
    assert llm.calls == 1


def test_fake_llm_runs_the_validators_too() -> None:
    with pytest.raises(LLMError) as caught:
        ask(FakeLLM(), validators=[lambda pet: "no dogs allowed"])
    assert caught.value.problems == ["no dogs allowed"]


def test_steadfast_llm_sends_the_problems_back_and_retries() -> None:
    llm, provider = scripted_llm(['{"name": "Rex", "legs": 3}', '{"name": "Rex", "legs": 4}'])

    assert ask(llm) == Pet(name="Rex", legs=4)
    assert len(provider.prompts) == 2
    # The second prompt carries the model's own answer and what was wrong with it.
    assert "legs must be 4" in provider.prompts[1]
    llm.close()


def test_steadfast_llm_gives_up_after_three_tries() -> None:
    llm, provider = scripted_llm(["not json", '{"name": "Rex"}', '{"name": "Rex", "legs": 5}'])

    with pytest.raises(LLMError) as caught:
        ask(llm)
    assert caught.value.problems == ["legs must be 4"]
    assert len(provider.prompts) == 3
    llm.close()


def test_steadfast_llm_runs_each_call_in_the_callers_log_context() -> None:
    # The worker binds job_id + correlation_id per job; Steadfast's log lines must carry
    # the current job's, not the first job's (the event loop is reused across jobs).
    llm, _ = scripted_llm(['{"name": "Rex", "legs": 4}', '{"name": "Rex", "legs": 4}'])
    seen = []

    def note_job(_pet: Pet) -> None:
        seen.append(structlog.contextvars.get_contextvars().get("job_id"))

    for job_id in (1, 2):
        with structlog.contextvars.bound_contextvars(job_id=job_id):
            ask(llm, validators=[note_job])
    assert seen == [1, 2]
    llm.close()


def test_close_can_be_called_twice() -> None:
    llm, _ = scripted_llm([])
    llm.close()
    llm.close()


def test_make_llm_fake_by_default(settings: Settings) -> None:
    assert isinstance(make_llm(settings), FakeLLM)


def test_make_llm_groq_needs_a_key(settings: Settings) -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        make_llm(settings.model_copy(update={"llm_mode": "groq"}))


def test_make_llm_openai_needs_a_key(settings: Settings) -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_llm(settings.model_copy(update={"llm_mode": "openai"}))


def test_make_llm_groq_uses_the_default_model(settings: Settings) -> None:
    llm = make_llm(settings.model_copy(update={"llm_mode": "groq", "groq_api_key": "test-key"}))

    assert isinstance(llm, SteadfastLLM)
    assert llm.client.providers["groq"].model == "openai/gpt-oss-20b"
    assert llm.params == {"max_tokens": 2048, "temperature": 0, "reasoning_effort": "low"}


def test_make_llm_takes_the_reasoning_effort_from_settings(settings: Settings) -> None:
    llm = make_llm(
        settings.model_copy(
            update={
                "llm_mode": "groq",
                "groq_api_key": "test-key",
                "llm_reasoning_effort": "medium",
            }
        )
    )
    assert isinstance(llm, SteadfastLLM)
    assert llm.params == {"max_tokens": 4096, "temperature": 0, "reasoning_effort": "medium"}
    llm.close()


def test_make_llm_sends_the_reasoning_effort_only_to_gpt_oss(settings: Settings) -> None:
    llm = make_llm(
        settings.model_copy(
            update={"llm_mode": "groq", "groq_api_key": "test-key", "llm_model": "other-model"}
        )
    )
    assert isinstance(llm, SteadfastLLM)
    assert llm.params == {"max_tokens": 2048, "temperature": 0}
    llm.close()


class FakeClock:
    """A clock that only moves when the budget sleeps (or a test moves it)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def budget(per_minute: int = 1000) -> tuple[TokenBudget, FakeClock]:
    clock = FakeClock()
    return TokenBudget(per_minute, clock=clock, sleep=clock.sleep), clock


def test_budget_lets_a_call_through_while_it_fits() -> None:
    tokens, clock = budget()
    tokens.spend(600)
    tokens.wait_for(400)
    assert clock.sleeps == []


def test_budget_waits_until_old_spending_leaves_the_minute() -> None:
    tokens, clock = budget()
    tokens.spend(600)
    clock.now += 20
    tokens.spend(300)

    tokens.wait_for(500)

    # 600 + 300 + 500 is over 1,000: wait the 40s until the 600 is a minute old.
    assert clock.sleeps == [40]
    assert tokens.used() == 300


def test_budget_forgets_spending_older_than_a_minute() -> None:
    tokens, clock = budget()
    tokens.spend(900)
    clock.now += 60
    assert tokens.used() == 0


def test_a_call_bigger_than_the_budget_waits_for_an_empty_minute() -> None:
    tokens, clock = budget()
    tokens.spend(100)
    tokens.wait_for(5000)
    assert clock.sleeps == [60]


def test_budget_ignores_calls_that_used_nothing() -> None:
    tokens, _ = budget()
    tokens.spend(0)
    assert not tokens.spent


def test_guess_tokens_counts_the_prompt_and_room_for_the_answer() -> None:
    assert guess_tokens("a" * 400, "b" * 800) == 300 + 400


def with_usage(llm: SteadfastLLM, per_call: int) -> SteadfastLLM:
    """Make `tokens_used()` grow by `per_call` input tokens on each call (the scripted
    provider reports none)."""
    calls = iter(range(1000))
    llm.tokens_used = lambda: ((next(calls) + 1) // 2 * per_call, 0)
    return llm


def test_steadfast_llm_waits_for_the_budget_and_spends_what_was_used() -> None:
    llm, _ = scripted_llm(['{"name": "Rex", "legs": 4}'])
    llm.budget, clock = budget()
    with_usage(llm, 700)
    llm.budget.spend(800)

    ask(llm)

    assert clock.sleeps == [60]  # 800 + the guess (~410) didn't fit
    assert [tokens for _, tokens in llm.budget.spent] == [700]


def test_steadfast_llm_spends_the_tokens_of_a_failed_call_too() -> None:
    llm, _ = scripted_llm(["not json", '{"name": "Rex"}', '{"name": "Rex", "legs": 5}'])
    llm.budget, _ = budget()
    with_usage(llm, 250)

    with pytest.raises(LLMError):
        ask(llm)

    assert [tokens for _, tokens in llm.budget.spent] == [250]


def test_make_llm_groq_has_the_free_tier_budget(settings: Settings) -> None:
    llm = make_llm(settings.model_copy(update={"llm_mode": "groq", "groq_api_key": "test-key"}))
    assert isinstance(llm, SteadfastLLM)
    assert llm.budget is not None and llm.budget.per_minute == 8000
    llm.close()


def test_make_llm_takes_the_budget_from_settings(settings: Settings) -> None:
    llm = make_llm(
        settings.model_copy(
            update={"llm_mode": "groq", "groq_api_key": "test-key", "llm_tokens_per_minute": 30000}
        )
    )
    assert isinstance(llm, SteadfastLLM)
    assert llm.budget is not None and llm.budget.per_minute == 30000
    llm.close()
