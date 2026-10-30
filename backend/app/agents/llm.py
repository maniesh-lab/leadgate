"""The agents' only door to a language model, and the only file that imports Steadfast.

Nodes call `llm.structured(...)`: a prompt goes in, a validated Pydantic object comes
out. Steadfast checks the answer against the schema and the caller's validators; when
something is wrong it sends the model its own answer plus the problems and asks again,
up to MAX_ATTEMPTS tries. If it still fails, `structured()` raises `LLMError` with the
last problems, and the node decides what to do (e.g. extraction marks itself not ok).

`make_llm(settings)` picks the implementation from LLM_MODE:
- `fake`: `FakeLLM`, no network. It returns the `fake_answer` the node passes in (a
  plain, rule-based answer built from the data), after running the same validators.
  Used by tests, CI, e2e and offline demos.
- `groq` / `openai`: `SteadfastLLM` on the real API (Groq's free tier by default).

Tests that need the real retry loop use `scripted_llm()`: a `SteadfastLLM` on a pretend
provider that replays canned replies, so no request ever leaves the machine.

Steadfast is async only and the agents are sync (like the DB layer), so `SteadfastLLM`
keeps one event loop (`asyncio.Runner`) and runs each call on it.

With Langfuse on, each `SteadfastLLM` call is a generation (prompt, answer, model,
tokens) under the node's span (`app.observability.tracing`). It's opened here, outside
the event loop, so it lands under the node that asked.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel
from steadfast import SteadfastClient, SteadfastError, ValidationRetryExceeded

from app.config import Settings
from app.observability.tracing import Tracer

DEFAULT_MODELS = {"groq": "openai/gpt-oss-20b", "openai": "gpt-4o-mini"}
# Calls per minute: Groq's free tier allows 30 for the gpt-oss models (checked 2026-10-28).
RATE_PER_MINUTE = {"groq": 30, "openai": 60}
# Tries per answer: the first one plus two corrections.
MAX_ATTEMPTS = 3
# Tries per request when the API is busy or down (429, 5xx, timeouts).
MAX_RETRIES = 4
# gpt-oss models think before they answer, and the thinking counts as output tokens.
MAX_TOKENS = 2048

# A check on a parsed answer: returns None when fine, or a sentence saying what's wrong
# (that sentence is sent back to the model).
Validator = Callable[[Any], str | None]


class LLMError(Exception):
    """The model gave no usable answer: still invalid after every try, or the API failed."""

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class LLM(ABC):
    """What the nodes use. `FakeLLM` and `SteadfastLLM` are the two implementations."""

    @abstractmethod
    def structured[T: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        fake_answer: Callable[[], T],
        validators: Sequence[Validator] = (),
    ) -> T:
        """Ask for a JSON answer matching `schema` that passes every validator.

        `fake_answer` is only used by `FakeLLM`; the real implementations ignore it.
        Raises LLMError when no valid answer comes back.
        """

    def close(self) -> None:
        """Free what the LLM holds (the event loop). Safe to call more than once."""
        return None  # nothing to free unless a subclass holds something


class FakeLLM(LLM):
    """No model at all: returns the caller's rule-based answer, checked like a real one."""

    def __init__(self) -> None:
        self.calls = 0

    def structured[T: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        fake_answer: Callable[[], T],
        validators: Sequence[Validator] = (),
    ) -> T:
        self.calls += 1
        answer = fake_answer()
        problems = [problem for check in validators if (problem := check(answer))]
        if problems:
            raise LLMError("the fake answer failed its checks", problems)
        return answer


class SteadfastLLM(LLM):
    """A real model behind Steadfast (retries, rate limit, validate-and-retry)."""

    def __init__(
        self,
        client: SteadfastClient,
        provider: str,
        *,
        model: str | None = None,
        tracer: Tracer | None = None,
        **params: Any,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model = model  # only for the trace
        self.tracer = tracer or Tracer()
        # Sent with every request, e.g. max_tokens and temperature.
        self.params = params
        self._runner = asyncio.Runner()

    def structured[T: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        fake_answer: Callable[[], T],
        validators: Sequence[Validator] = (),
    ) -> T:
        call = self.client.complete(
            self.provider,
            prompt,
            schema=schema,
            system=system,
            validators=list(validators),
            max_attempts=MAX_ATTEMPTS,
            **self.params,
        )
        trace_input = {"system": system, "prompt": prompt}
        with self.tracer.generation(schema.__name__, model=self.model, input=trace_input) as span:
            used_before = self.tokens_used()
            try:
                answer = self._runner.run(call)
            except ValidationRetryExceeded as exc:
                span.error(str(exc))
                raise LLMError(str(exc), exc.errors) from exc
            except SteadfastError as exc:
                span.error(str(exc))
                raise LLMError(f"the LLM call failed: {exc}", [str(exc)]) from exc
            used = [
                now - before for now, before in zip(self.tokens_used(), used_before, strict=True)
            ]
            span.output(answer.model_dump(), usage={"input": used[0], "output": used[1]})
            return answer

    def tokens_used(self) -> tuple[int, int]:
        """(input, output) tokens this provider has used so far (Steadfast keeps the count)."""
        usage = self.client.usage.get(self.provider)
        return (usage.input_tokens, usage.output_tokens) if usage else (0, 0)

    def close(self) -> None:
        self._runner.close()


class ScriptedProvider:
    """A pretend Steadfast provider that answers with `replies`, in order (tests only).

    Steadfast accepts any object with a `name` and an async `complete(prompt)`.
    `prompts` keeps every prompt it was sent, so tests can see the retry feedback.
    """

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **params: Any) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def scripted_llm(replies: list[str]) -> tuple[SteadfastLLM, ScriptedProvider]:
    """A `SteadfastLLM` on a `ScriptedProvider`: the real retry loop with canned replies."""
    provider = ScriptedProvider(replies)
    client = SteadfastClient(max_retries=1)
    client.register(provider, rate=1000, period=1)
    return SteadfastLLM(client, provider.name), provider


def make_llm(settings: Settings, tracer: Tracer | None = None) -> LLM:
    """The LLM for LLM_MODE. Raises ValueError when the mode's API key is missing.

    `tracer` (the worker's) puts each real call in Langfuse; FakeLLM calls aren't traced.

    LLM_MODE=openai also needs the optional openai SDK (`pip install -e ".[openai]"`); the
    Docker image is built without it, so there it raises ImportError.
    """
    if settings.llm_mode == "fake":
        return FakeLLM()

    model = settings.llm_model or DEFAULT_MODELS[settings.llm_mode]
    if settings.llm_mode == "groq":
        if not settings.groq_api_key:
            raise ValueError("LLM_MODE=groq needs GROQ_API_KEY")
        from steadfast.providers.groq_provider import GroqProvider

        provider = GroqProvider(api_key=settings.groq_api_key, model=model)
    else:
        if not settings.openai_api_key:
            raise ValueError("LLM_MODE=openai needs OPENAI_API_KEY")
        # Imported here: the openai SDK is an optional extra (pip install -e ".[openai]").
        from steadfast.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key=settings.openai_api_key, model=model)

    client = SteadfastClient(max_retries=MAX_RETRIES)
    client.register(provider, rate=RATE_PER_MINUTE[settings.llm_mode], period=60)
    return SteadfastLLM(
        client, provider.name, model=model, tracer=tracer, max_tokens=MAX_TOKENS, temperature=0
    )
