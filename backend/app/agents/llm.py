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
- `groq` / `openai` / `anthropic`: `SteadfastLLM` on the real API (Groq's free tier by
  default; `anthropic` is Claude, Claude Haiku 5.5 unless LLM_MODEL says other).

Tests that need the real retry loop use `scripted_llm()`: a `SteadfastLLM` on a pretend
provider that replays canned replies, so no request ever leaves the machine.

Steadfast is async only and the agents are sync (like the DB layer), so `SteadfastLLM`
keeps one event loop (`asyncio.Runner`) and runs each call on it.

Steadfast limits calls per minute, but Groq's free tier also limits tokens per minute
(8,000 for gpt-oss-20b), and an extraction can use 2,000-3,000. Past that Groq answers 429
and Steadfast waits and retries (6 and 20 retries in two 20-lead evals). So a
`TokenBudget` (LLM_TOKENS_PER_MINUTE) counts the tokens used in the last minute and waits
before a call that would go over, instead of letting it fail.

With Langfuse on, each `SteadfastLLM` call is a generation (prompt, answer, model,
tokens) under the node's span (`app.observability.tracing`). It's opened here, outside
the event loop, so it lands under the node that asked.
"""

import asyncio
import contextvars
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

import structlog
from pydantic import BaseModel
from steadfast import SteadfastClient, SteadfastError, ValidationRetryExceeded

from app.config import Settings
from app.observability.tracing import Tracer

log = structlog.get_logger(__name__)

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "openai": "gpt-4o-mini",
    # Fast and cheap ($0.10 / $0.50 per million tokens in / out, 2026-11-08), Boss's pick.
    "anthropic": "claude-haiku-5-5",
}
# Calls per minute: Groq's free tier allows 30 for the gpt-oss models (checked 2026-10-28);
# Anthropic's first paid tier allows 50.
RATE_PER_MINUTE = {"groq": 30, "openai": 60, "anthropic": 50}
# Tries per answer: the first one plus two corrections.
MAX_ATTEMPTS = 3
# Tries per request when the API is busy or down (429, 5xx, timeouts).
MAX_RETRIES = 4
# gpt-oss models think before they answer, and the thinking counts as output tokens, so the
# limit grows with LLM_REASONING_EFFORT. At medium effort and 2,048 the model spent the whole
# limit thinking and answered with nothing (2,046 of 2,048 on real leads); low used 23-124.
# Austin eval, 7 leads with services (2026-11-08): low 79% precision / 79% recall at 18.7k
# tokens; medium 90% / 93% at 34.6k, and one reply was still cut off at 4,096 and retried.
MAX_TOKENS = {"low": 2048, "medium": 4096, "high": 8192}
# Tokens per minute when LLM_TOKENS_PER_MINUTE is empty: Groq's free tier for gpt-oss-20b
# (checked 2026-11-08), used whatever LLM_MODEL is; set the setting for a model or plan with
# another limit. No budget on openai unless the setting is set.
TOKENS_PER_MINUTE = {"groq": 8000}
# A call's size is only known afterwards, so the budget guesses it beforehand: about 4
# characters per token for the prompt, plus room for the answer (thinking + JSON).
CHARS_PER_TOKEN = 4
ANSWER_TOKENS_GUESS = 400
# Claude always thinks first on the current models, and the thinking counts toward
# max_tokens; LLM_REASONING_EFFORT sets how much (`output_config.effort`). They take no
# temperature (it's a 400), so none is sent. Room for thinking plus the JSON answer, kept
# under the SDK's limit for a request that isn't streamed.
CLAUDE_MAX_TOKENS = 16000
CLAUDE_TIMEOUT_SECONDS = 120.0
# Claude models that can hand a request they decline to another model on Anthropic's side
# (server-side fallback, "default" picks the model); Claude Haiku 5.5 can't.
CLAUDE_FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
}
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

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


class TokenBudget:
    """Keeps the tokens used in any 60 seconds under `per_minute` by waiting before a call.

    `wait_for(guess)` before a call, `spend(used)` after it with what it really used
    (validation retries included). One per process: the worker runs one job at a time.
    """

    WINDOW_SECONDS = 60.0

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_minute = per_minute
        # Tests pass a fake clock and sleep.
        self.clock = clock
        self.sleep = sleep
        self.spent: deque[tuple[float, int]] = deque()  # (when, tokens), oldest first

    def used(self) -> int:
        """Tokens spent in the last minute."""
        cutoff = self.clock() - self.WINDOW_SECONDS
        while self.spent and self.spent[0][0] <= cutoff:
            self.spent.popleft()
        return sum(tokens for _, tokens in self.spent)

    def wait_for(self, tokens: int) -> None:
        """Wait until `tokens` more fit in the last minute (a call bigger than the whole
        budget waits for an empty minute)."""
        needed = min(tokens, self.per_minute)
        while (used := self.used()) + needed > self.per_minute:
            wait = self.spent[0][0] + self.WINDOW_SECONDS - self.clock()
            log.info("llm_token_budget_wait", used=used, needed=needed, wait_seconds=round(wait, 1))
            self.sleep(max(wait, 0.0))

    def spend(self, tokens: int) -> None:
        if tokens > 0:
            self.spent.append((self.clock(), tokens))


def guess_tokens(system: str, prompt: str) -> int:
    """What a call will roughly use, before it's sent (the budget corrects it afterwards)."""
    return (len(system) + len(prompt)) // CHARS_PER_TOKEN + ANSWER_TOKENS_GUESS


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
        budget: TokenBudget | None = None,
        **params: Any,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model = model  # only for the trace
        self.tracer = tracer or Tracer()
        # None = no tokens-per-minute limit (Steadfast still limits calls per minute).
        self.budget = budget
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
        if self.budget:
            self.budget.wait_for(guess_tokens(system, prompt))
        with self.tracer.generation(schema.__name__, model=self.model, input=trace_input) as span:
            used_before = self.tokens_used()
            try:
                # A fresh copy of the caller's context each time: on its own the runner
                # keeps the first call's, so Steadfast's log lines would carry the first
                # job's correlation_id + job_id forever.
                answer = self._runner.run(call, context=contextvars.copy_context())
            except ValidationRetryExceeded as exc:
                span.error(str(exc))
                raise LLMError(str(exc), exc.errors) from exc
            except SteadfastError as exc:
                span.error(str(exc))
                raise LLMError(f"the LLM call failed: {exc}", [str(exc)]) from exc
            finally:
                # Failed tries used tokens too (each wrong answer that was sent back).
                used = [
                    now - before
                    for now, before in zip(self.tokens_used(), used_before, strict=True)
                ]
                if self.budget:
                    self.budget.spend(sum(used))
            span.output(answer.model_dump(), usage={"input": used[0], "output": used[1]})
            return answer

    def tokens_used(self) -> tuple[int, int]:
        """(input, output) tokens this provider has used so far (Steadfast keeps the count)."""
        usage = self.client.usage.get(self.provider)
        return (usage.input_tokens, usage.output_tokens) if usage else (0, 0)

    def close(self) -> None:
        """Close the event loop. Inside a running loop (the API shutting down with
        RUN_WORKER_IN_API) `Runner.close()` refuses to run, so a helper thread does it."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # no loop running here: the usual case (the worker process)
            self._runner.close()
            return
        closer = threading.Thread(target=self._runner.close, name="llm-close")
        closer.start()
        closer.join()


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
    A real LLM gets a `TokenBudget` from LLM_TOKENS_PER_MINUTE (empty = 8,000 on groq).
    """
    if settings.llm_mode == "fake":
        return FakeLLM()

    mode = settings.llm_mode
    model = settings.llm_model or DEFAULT_MODELS[mode]
    effort = settings.llm_reasoning_effort
    # Each SDK is imported only for its own mode.
    if mode == "groq":
        if not settings.groq_api_key:
            raise ValueError("LLM_MODE=groq needs GROQ_API_KEY")
        from steadfast.providers.groq_provider import GroqProvider

        provider: Any = GroqProvider(api_key=settings.groq_api_key, model=model)
    elif mode == "openai":
        if not settings.openai_api_key:
            raise ValueError("LLM_MODE=openai needs OPENAI_API_KEY")
        from steadfast.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key=settings.openai_api_key, model=model)
    else:
        if not settings.anthropic_api_key:
            raise ValueError("LLM_MODE=anthropic needs ANTHROPIC_API_KEY")
        from steadfast.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=settings.anthropic_api_key, model=model, timeout=CLAUDE_TIMEOUT_SECONDS
        )

    client = SteadfastClient(max_retries=MAX_RETRIES)
    client.register(provider, rate=RATE_PER_MINUTE[mode], period=60)
    if mode == "anthropic":
        params = claude_params(model, effort)
    else:
        params = {"max_tokens": MAX_TOKENS[effort], "temperature": 0}
        # Only these models take the setting; others may refuse a request that has it.
        if mode == "groq" and "gpt-oss" in model:
            params["reasoning_effort"] = effort
    per_minute = settings.llm_tokens_per_minute or TOKENS_PER_MINUTE.get(settings.llm_mode)
    budget = TokenBudget(per_minute) if per_minute else None
    return SteadfastLLM(client, provider.name, model=model, tracer=tracer, budget=budget, **params)


def claude_params(model: str, effort: str) -> dict[str, Any]:
    """What every Claude request sends: room for thinking, the effort level, no temperature,
    and a server-side fallback on the models that have one (a declined request then runs on
    another Claude model instead of failing the lead's extraction)."""
    params: dict[str, Any] = {"max_tokens": CLAUDE_MAX_TOKENS, "output_config": {"effort": effort}}
    if model in CLAUDE_FALLBACK_MODELS:
        params["extra_headers"] = {"anthropic-beta": CLAUDE_FALLBACK_BETA}
        params["extra_body"] = {"fallbacks": "default"}
    return params
