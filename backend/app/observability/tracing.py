"""Langfuse tracing: one trace per run item, a span per node, a generation per LLM call.

Off by default: `make_tracer(settings)` gives the no-op `Tracer` unless both
LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set; then it's `LangfuseTracer`.
Tests, CI and demos without keys never touch Langfuse.

How the pieces fit:
- the trace id comes from the item's thread id (`Langfuse.create_trace_id(seed=...)`),
  so the first job (up to the approval gate) and the resume job, even in another
  process after a crash, add to the same trace. It's saved on every `run_steps` row.
- `app.agents.tracking.run_tracked()` opens a `node()` span around each node
- `app.agents.llm.SteadfastLLM` opens a `generation()` inside it for each LLM call
  (prompt, answer, model, tokens), so it shows up under its node
- each trace is named after the thread id and put in the Langfuse session `run-<id>`,
  so a run's leads are listed together

Spans go to Langfuse in the background (the SDK batches them); `close()` sends what's
left. The SDK is OpenTelemetry-based; this module is the only place that imports it.
Called by the worker (`app.worker.main.open_worker`).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from langfuse import Langfuse, propagate_attributes

from app.config import Settings

log = structlog.get_logger(__name__)


class Span:
    """What a node or LLM call reports back. This one does nothing (tracing is off)."""

    def output(self, value: Any, usage: dict[str, int] | None = None) -> None:
        return None

    def error(self, message: str) -> None:
        return None


class Tracer:
    """Tracing off: every method does nothing, so callers never need to check."""

    def trace_id(self, thread_id: str) -> str | None:
        """The item's trace id, or None when tracing is off."""
        return None

    @contextmanager
    def node(self, name: str, *, thread_id: str, run_id: int | None, input: Any) -> Iterator[Span]:
        """A span for one node call, in the item's trace."""
        yield Span()

    @contextmanager
    def generation(self, name: str, *, model: str | None, input: Any) -> Iterator[Span]:
        """A generation for one LLM call, under the current node's span."""
        yield Span()

    def close(self) -> None:
        return None


class LangfuseSpan(Span):
    def __init__(self, observation: Any) -> None:
        self.observation = observation

    def output(self, value: Any, usage: dict[str, int] | None = None) -> None:
        self.observation.update(output=value, usage_details=usage)

    def error(self, message: str) -> None:
        self.observation.update(level="ERROR", status_message=message)


class LangfuseTracer(Tracer):
    def __init__(self, langfuse: Langfuse) -> None:
        self.langfuse = langfuse

    def trace_id(self, thread_id: str) -> str:
        return Langfuse.create_trace_id(seed=thread_id)

    @contextmanager
    def node(self, name: str, *, thread_id: str, run_id: int | None, input: Any) -> Iterator[Span]:
        session = f"run-{run_id}" if run_id is not None else None
        with (
            propagate_attributes(trace_name=thread_id, session_id=session),
            self.langfuse.start_as_current_observation(
                trace_context={"trace_id": self.trace_id(thread_id)},
                name=name,
                as_type="span",
                input=input,
            ) as observation,
        ):
            yield LangfuseSpan(observation)

    @contextmanager
    def generation(self, name: str, *, model: str | None, input: Any) -> Iterator[Span]:
        with self.langfuse.start_as_current_observation(
            name=name, as_type="generation", model=model, input=input
        ) as observation:
            yield LangfuseSpan(observation)

    def close(self) -> None:
        self.langfuse.shutdown()  # sends the spans still waiting


def make_tracer(settings: Settings) -> Tracer:
    """`LangfuseTracer` when both Langfuse keys are set, else the no-op `Tracer`."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        log.info("tracing_off", note="LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
        return Tracer()
    log.info("tracing_langfuse", base_url=settings.langfuse_base_url)
    return LangfuseTracer(
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
        )
    )
