"""Tests for the LLM wrapper (`app.agents.llm`): fake mode, the retry loop, picking the mode.

No network: the retry loop runs on `ScriptedProvider`, which replays canned replies.
"""

import pytest
from pydantic import BaseModel

from app.agents.llm import FakeLLM, LLMError, SteadfastLLM, make_llm, scripted_llm
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
    assert llm.params == {"max_tokens": 2048, "temperature": 0}
    llm.close()
