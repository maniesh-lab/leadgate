"""Tests for `app.agents.tracking`: run_steps + audit rows for every node.

Runs the real graph through `tests.agent_world` (in-memory checkpointer, SQLite, fakes).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.nodes.score import MAX_EXPLANATION_CHARS
from app.agents.runner import resume_lead, start_lead
from app.agents.tracking import MAX_TEXT, compact
from app.db.models import AuditLog, RunStep, StepStatus
from tests.agent_world import World, add_lead


@pytest.fixture
def world(db: Session) -> World:
    return World(db)


def steps(db: Session) -> list[tuple[str, str]]:
    rows = db.scalars(select(RunStep).order_by(RunStep.id))
    return [(row.node, row.status.value) for row in rows]


def actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_every_node_up_to_the_gate_leaves_a_step_and_an_audit_row(world: World) -> None:
    world.start(add_lead(world.db))

    assert steps(world.db) == [
        ("enrich", "completed"),
        ("extract", "completed"),
        ("check", "completed"),
        ("score", "completed"),
        ("approval_gate", "started"),  # waiting for the human
    ]
    assert actions(world.db) == [
        "lead.enriched",
        "lead.extracted",
        "lead.checked",
        "lead.scored",
        "approval.requested",
    ]


def test_resume_completes_the_open_gate_step_instead_of_adding_one(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)

    resume_lead(world.graph, world.context(), f"lead-{lead.id}", decision="approved", reviewer_id=1)

    assert steps(world.db)[4:] == [
        ("approval_gate", "completed"),
        ("route", "completed"),
        ("act", "completed"),
    ]
    assert actions(world.db)[-4:] == ["lead.decided", "lead.routed", "lead.acted", "crm.written"]
    written = world.db.scalars(select(AuditLog).where(AuditLog.action == "crm.written")).one()
    assert written.details == {"crm_record_id": "fake-crm-1"}


def test_step_rows_hold_compact_input_output_and_timing(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)

    score = world.db.scalars(select(RunStep).where(RunStep.node == "score")).one()
    assert score.output["score"] == 60
    assert score.input["lead_id"] == lead.id
    # The website text is cut so rows stay small.
    assert len(score.input["enrichment"]["text"]) < MAX_TEXT + 30
    assert score.duration_ms is not None and score.finished_at is not None

    audit = world.db.scalars(select(AuditLog).where(AuditLog.action == "lead.scored")).one()
    assert (audit.entity_type, audit.entity_id) == ("lead", lead.id)
    assert audit.run_id == world.run.id
    assert audit.actor == "score"
    assert audit.correlation_id == "test-correlation"


def test_a_failing_node_is_recorded_as_failed_and_its_changes_rolled_back(world: World) -> None:
    lead = add_lead(world.db)
    world.web.routes = {}  # no site at all: enrich still works (records the failure)
    world.llm.structured = broken_llm  # extract's LLM call blows up

    with pytest.raises(RuntimeError, match="LLM down"):
        world.start(lead)

    assert steps(world.db) == [("enrich", "completed"), ("extract", "failed")]
    failed = world.db.scalars(select(RunStep).where(RunStep.node == "extract")).one()
    assert failed.error == "RuntimeError: LLM down"
    assert actions(world.db)[-1] == "node.failed"


def test_a_step_left_open_by_a_crash_is_reused_on_retry(world: World) -> None:
    lead = add_lead(world.db)
    item = world.add_item(lead)
    # What a worker killed mid-enrich leaves behind.
    world.db.add(RunStep(run_item_id=item.id, node="enrich", status=StepStatus.STARTED))
    world.db.commit()

    start_lead(
        world.graph,
        world.context(),
        thread_id=item.thread_id,
        lead_id=lead.id,
        campaign_id=world.campaign.id,
        rubric_id=world.campaign.rubric_id,
        run_item_id=item.id,
    )

    assert steps(world.db)[0] == ("enrich", "completed")
    assert [node for node, _ in steps(world.db)].count("enrich") == 1


def test_without_a_run_item_nothing_is_tracked(world: World) -> None:
    lead = add_lead(world.db)

    start_lead(
        world.graph,
        world.context(),
        thread_id="script",
        lead_id=lead.id,
        campaign_id=world.campaign.id,
        rubric_id=world.campaign.rubric_id,
    )

    assert steps(world.db) == []
    assert actions(world.db) == []


def test_compact_cuts_long_strings_and_lists() -> None:
    value = {"text": "x" * 1000, "items": list(range(25)), "n": 3}

    small = compact(value)

    assert small["text"] == "x" * 500 + "… (+500 chars)"
    assert small["items"] == [*range(20), "… (+5 more)"]
    assert small["n"] == 3


def test_a_whole_score_explanation_fits_in_a_step() -> None:
    # The API shows the explanation from the score step's output, so it must not be cut.
    assert MAX_TEXT >= MAX_EXPLANATION_CHARS


def broken_llm(*_args, **_kwargs):
    raise RuntimeError("LLM down")
