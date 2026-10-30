"""Tests for Langfuse tracing. No network: the spans go to OpenTelemetry's in-memory exporter.

Langfuse keeps one client per public key for the whole process, so the tests share one.
"""

from collections.abc import Iterator

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.llm import LLMError, scripted_llm
from app.agents.runner import resume_lead
from app.config import Settings
from app.db.models import RunStep
from app.observability import tracing
from app.observability.tracing import LangfuseTracer, Tracer, make_tracer
from tests.agent_world import World, add_lead

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module")
def langfuse() -> Iterator[Langfuse]:
    client = Langfuse(
        public_key="pk-lf-leadgate-tests",
        secret_key="sk-lf-leadgate-tests",
        base_url="http://langfuse.test",
        span_exporter=EXPORTER,
    )
    yield client
    client.shutdown()


@pytest.fixture
def tracer(langfuse: Langfuse) -> LangfuseTracer:
    langfuse.flush()  # spans an earlier test left unsent would land in this test's list
    EXPORTER.clear()
    return LangfuseTracer(langfuse)


def finished(tracer: LangfuseTracer) -> list[ReadableSpan]:
    tracer.langfuse.flush()
    return list(EXPORTER.get_finished_spans())


def attribute(span: ReadableSpan, name: str) -> object:
    return span.attributes.get(f"langfuse.observation.{name}")


def test_tracing_is_off_without_both_keys(settings: Settings) -> None:
    assert type(make_tracer(settings)) is Tracer
    half = settings.model_copy(update={"langfuse_public_key": "pk-lf-x"})
    assert type(make_tracer(half)) is Tracer


def test_both_keys_turn_langfuse_on(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[dict] = []
    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: made.append(kwargs))
    keys = {"langfuse_public_key": "pk-lf-x", "langfuse_secret_key": "sk-lf-x"}

    tracer = make_tracer(settings.model_copy(update=keys))

    assert isinstance(tracer, LangfuseTracer)
    assert made == [
        {"public_key": "pk-lf-x", "secret_key": "sk-lf-x", "base_url": "https://cloud.langfuse.com"}
    ]


def test_the_off_tracer_does_nothing() -> None:
    tracer = Tracer()
    assert tracer.trace_id("run-1-lead-1") is None
    with tracer.node("enrich", thread_id="t", run_id=1, input={}) as span:
        span.output({"x": 1})
        span.error("ignored")
    with tracer.generation("LLMFields", model=None, input={}) as span:
        span.output({}, usage={"input": 1, "output": 1})
    tracer.close()


def test_a_lead_is_one_trace_with_a_span_per_node(world: World, tracer: LangfuseTracer) -> None:
    world.tracer = tracer
    lead = add_lead(world.db)
    world.start(lead)
    resume_lead(world.graph, world.context(), f"lead-{lead.id}", decision="approved", reviewer_id=1)

    spans = finished(tracer)
    trace_id = Langfuse.create_trace_id(seed=f"lead-{lead.id}")
    assert [span.name for span in spans] == [
        "enrich",
        "extract",
        "check",
        "score",
        "approval_gate",  # the pause
        "approval_gate",  # the resume
        "route",
        "act",
    ]
    assert {format(span.context.trace_id, "032x") for span in spans} == {trace_id}
    assert {span.attributes["session.id"] for span in spans} == {f"run-{world.run.id}"}
    assert {span.attributes["langfuse.trace.name"] for span in spans} == {f"lead-{lead.id}"}
    # Waiting for a human isn't an error.
    assert attribute(spans[4], "output") == '{"paused": "waiting for a human decision"}'
    assert not any(attribute(span, "level") == "ERROR" for span in spans)
    # Every step row knows its trace.
    step_traces = set(world.db.scalars(select(RunStep.trace_id)))
    assert step_traces == {trace_id}


def test_a_failing_node_is_an_error_span(
    world: World, tracer: LangfuseTracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.tracer = tracer

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("site exploded")

    monkeypatch.setattr("app.agents.nodes.enrich.enrich_lead", broken)
    lead = add_lead(world.db)

    with pytest.raises(RuntimeError, match="site exploded"):
        world.start(lead)

    (span,) = finished(tracer)
    assert span.name == "enrich"
    assert attribute(span, "level") == "ERROR"
    assert attribute(span, "status_message") == "RuntimeError: site exploded"


class Pet(BaseModel):
    name: str


def test_each_llm_call_is_a_generation(tracer: LangfuseTracer) -> None:
    llm, _provider = scripted_llm(['{"name": "Rex"}'])
    llm.tracer, llm.model = tracer, "openai/gpt-oss-20b"

    llm.structured(
        system="Be short.", prompt="A dog?", schema=Pet, fake_answer=lambda: Pet(name="")
    )

    (span,) = finished(tracer)
    assert (span.name, attribute(span, "type")) == ("Pet", "generation")
    assert attribute(span, "model.name") == "openai/gpt-oss-20b"
    assert attribute(span, "input") == '{"system": "Be short.", "prompt": "A dog?"}'
    assert attribute(span, "output") == '{"name": "Rex"}'
    assert attribute(span, "usage_details") == '{"input": 0, "output": 0}'
    llm.close()


def test_a_failed_llm_call_is_an_error_generation(tracer: LangfuseTracer) -> None:
    llm, _provider = scripted_llm(["no", "still no", "never"])
    llm.tracer = tracer

    with pytest.raises(LLMError):
        llm.structured(system="", prompt="?", schema=Pet, fake_answer=lambda: Pet(name=""))

    (span,) = finished(tracer)
    assert attribute(span, "level") == "ERROR"
    llm.close()
