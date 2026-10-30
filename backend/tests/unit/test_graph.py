"""Tests for the whole lead graph (`app.agents.graph` + `app.agents.runner`).

Each test runs the real graph on the in-memory checkpointer, a SQLite DB and fakes
for the website, LLM, CRM and notifier (see `tests.agent_world`). No network.
"""

import json

import pytest
from langgraph.runtime import Runtime
from sqlalchemy.orm import Session

from app.agents.graph import build_graph
from app.agents.llm import scripted_llm
from app.agents.nodes.act import act
from app.agents.runner import (
    FINISHED,
    NEW,
    PAUSED,
    UNFINISHED,
    continue_lead,
    current_state,
    resume_lead,
    start_lead,
    thread_status,
    waiting_for,
)
from app.db.models import Lead, LeadStatus, RunItem
from app.services.notify import Channel
from app.services.privacy import opt_out_lead
from tests.agent_world import World, add_lead


@pytest.fixture
def world(db: Session) -> World:
    return World(db)


def decide(world: World, lead: Lead, decision: str, reason: str | None = None):
    return resume_lead(
        world.graph,
        world.context(),
        f"lead-{lead.id}",
        decision=decision,
        reason=reason,
        reviewer_id=1,
    )


def test_a_lead_runs_up_to_the_approval_gate_and_waits(world: World) -> None:
    lead = add_lead(world.db)

    state = world.start(lead)

    request = waiting_for(world.graph, f"lead-{lead.id}")
    assert request is not None
    assert request["name"] == "Smile Austin Dental"
    assert request["phone"] == "+15125550142"
    assert request["email"] == "info@smileaustin.test"
    assert request["score"] == 60  # general-b2b: phone 15 + email 20 + website 10 + category 15
    assert request["flags"] == []
    assert state["extraction_ok"] is True
    assert "route" not in state
    # Nothing has left the building yet.
    assert world.crm.writes == 0
    assert world.notifier.sent == []
    assert world.db.get(Lead, lead.id).status == LeadStatus.IN_REVIEW


def test_the_lead_row_gets_the_extracted_fields_and_score(world: World) -> None:
    lead = add_lead(world.db)

    world.start(lead)

    world.db.expire_all()
    saved = world.db.get(Lead, lead.id)
    assert saved.phone == "+15125550142"
    assert saved.email == "info@smileaustin.test"
    assert saved.city == "Austin"
    assert saved.category == "dentist"
    assert saved.score == 60
    assert saved.enrichment["signals"]["copyright_year"] == 2019


def test_approve_path_writes_the_crm_and_sends_one_message(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)

    state = decide(world, lead, "approved")

    assert state["route"] == "notify"
    assert state["crm_record_id"] == "fake-crm-1"
    assert state["notified"] is True
    assert world.crm.writes == 1
    assert world.crm.records[f"lead-{lead.id}"]["email"] == "info@smileaustin.test"
    assert len(world.notifier.sent) == 1
    channel, text = world.notifier.sent[0]
    assert channel == Channel.LEADS
    assert "Smile Austin Dental" in text and "60/100" in text
    world.db.expire_all()
    saved = world.db.get(Lead, lead.id)
    assert saved.status == LeadStatus.APPROVED
    assert saved.crm_record_id == "fake-crm-1"
    assert waiting_for(world.graph, f"lead-{lead.id}") is None


def test_reject_path_skips_every_side_effect(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)

    state = decide(world, lead, "rejected", reason="already a client")

    assert state["route"] == "skip"
    assert state["reason"] == "already a client"
    assert world.crm.writes == 0
    assert world.notifier.sent == []
    assert world.db.get(Lead, lead.id).status == LeadStatus.REJECTED


def test_a_rejection_without_a_reason_is_refused_and_the_lead_keeps_waiting(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)

    with pytest.raises(ValueError, match="needs a reason"):
        decide(world, lead, "rejected", reason="  ")

    assert waiting_for(world.graph, f"lead-{lead.id}") is not None
    state = decide(world, lead, "rejected", reason="too far away")
    assert state["route"] == "skip"


def test_an_unknown_decision_is_refused(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)

    with pytest.raises(ValueError, match="decision must be"):
        decide(world, lead, "maybe")


def test_resuming_a_thread_that_is_not_waiting_is_an_error(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)
    decide(world, lead, "approved")

    with pytest.raises(ValueError, match="not waiting"):
        decide(world, lead, "approved")


def test_duplicate_is_flagged_and_an_approval_escalates(world: World) -> None:
    first = add_lead(world.db)
    world.start(first)
    # The same practice again from another listing: same phone, written differently.
    second = add_lead(world.db, name="Smile Austin Dental Clinic", phone="+1 512 555 0142")

    world.start(second)
    state = decide(world, second, "approved")

    assert f"possible_duplicate:{first.id}" in state["flags"]
    assert state["route"] == "escalate"
    assert world.crm.writes == 0
    assert len(world.notifier.sent) == 1
    channel, text = world.notifier.sent[0]
    assert channel == Channel.ADMIN
    assert f"possible_duplicate:{first.id}" in text
    assert world.db.get(Lead, second.id).status == LeadStatus.ESCALATED


def test_category_mismatch_is_flagged_and_the_web_agency_gate_gives_zero(world: World) -> None:
    lead = add_lead(world.db, name="Taco Joint", category="restaurant", website=None)

    state = world.start(lead, rubric="web-agency")

    assert state["extracted"]["category"] == "restaurant"
    assert "category_mismatch" in state["flags"]
    assert "rubric_gate:category matches niche" in state["flags"]
    assert state["score"] == 0
    assert "required rule failed" in state["score_explanation"]


def test_closed_business_is_flagged(world: World) -> None:
    lead = add_lead(world.db, business_status="closed_permanently")

    state = world.start(lead)

    assert "closed" in state["flags"]


def test_web_agency_scores_an_old_site_as_a_hot_lead(world: World) -> None:
    lead = add_lead(world.db, rating=4.7, review_count=85)

    state = world.start(lead, rubric="web-agency")

    # rating 20 + reviews 15 + copyright 2019 is older than 3 years 20 (site has https + viewport)
    assert state["score"] == 55
    hits = {entry["rule"]: entry["hit"] for entry in state["score_breakdown"]}
    assert hits["copyright year older than 3 years"] is True
    assert hits["no website"] is False


def llm_reply(**fields) -> str:
    answer = {"email": None, "area": None, "city": "Austin", "country": "United States"}
    return json.dumps(answer | {"category": "dentist", "services": []} | fields)


EXPLANATION = json.dumps({"text": "Strong lead: it has a phone, an email and a website."})


def test_the_llm_fills_services_and_year_from_the_website(db: Session) -> None:
    reply = llm_reply(
        email="info@smileaustin.test",
        services=["teeth whitening", "dental implants"],
        established_year=1998,
    )
    llm, _ = scripted_llm([reply, EXPLANATION])
    world = World(db, llm=llm)
    lead = add_lead(db)

    state = world.start(lead)

    assert state["extraction_ok"] is True
    assert state["extracted"]["services"] == ["teeth whitening", "dental implants"]
    assert state["extracted"]["established_year"] == 1998
    assert state["extracted"]["country"] == "United States"
    assert state["score_explanation"].startswith("Strong lead")
    llm.close()


def test_an_invented_service_is_sent_back_and_fixed(db: Session) -> None:
    invented = llm_reply(services=["teeth whitening", "laser hair removal"])
    fixed = llm_reply(services=["teeth whitening"])
    llm, provider = scripted_llm([invented, fixed, EXPLANATION])
    world = World(db, llm=llm)

    state = world.start(add_lead(db))

    assert state["extraction_ok"] is True
    assert state["extracted"]["services"] == ["teeth whitening"]
    assert "laser hair removal" in provider.prompts[1]
    llm.close()


def test_extraction_failure_keeps_the_clean_fields_and_escalates(db: Session) -> None:
    # Three tries, the city is made up every time.
    made_up = llm_reply(city="Dallas")
    llm, _ = scripted_llm([made_up, made_up, made_up, EXPLANATION])
    world = World(db, llm=llm)
    lead = add_lead(db)

    state = world.start(lead)

    assert state["extraction_ok"] is False
    assert any("city 'Dallas'" in issue for issue in state["extraction_issues"])
    # The rule-based fields are kept: the phone, the best email, the source's city.
    assert state["extracted"]["phone"] == "+15125550142"
    assert state["extracted"]["email"] == "info@smileaustin.test"
    assert state["extracted"]["city"] == "Austin"
    assert waiting_for(world.graph, f"lead-{lead.id}")["extraction_ok"] is False

    state = decide(world, lead, "approved")
    assert state["route"] == "escalate"
    assert "extraction failed" in world.notifier.sent[0][1]
    llm.close()


def test_a_bad_explanation_falls_back_to_the_template(db: Session) -> None:
    wrong_number = json.dumps({"text": "This lead scores 95 out of 100."})
    llm, _ = scripted_llm(
        [llm_reply(email="info@smileaustin.test"), wrong_number, wrong_number, wrong_number]
    )
    world = World(db, llm=llm)

    state = world.start(add_lead(db))

    assert state["score_explanation"].startswith("Score 60/100: has phone (+15)")
    llm.close()


def test_resume_in_a_new_process_does_not_rerun_finished_nodes(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)
    requests_before = len(world.web.requests)
    llm_calls_before = world.llm.calls

    # A "restarted" worker: a new graph object on the same checkpointer.
    world.graph = build_graph(world.checkpointer)
    state = decide(world, lead, "approved")

    assert state["route"] == "notify"
    assert len(world.web.requests) == requests_before  # enrich did not run again
    assert world.llm.calls == llm_calls_before  # neither did extract or score
    assert world.crm.writes == 1


def test_act_does_nothing_twice(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)
    decide(world, lead, "approved")
    state = current_state(world.graph, f"lead-{lead.id}")

    # Run act again on the finished state, as a retry after a crash would.
    update = act(state, Runtime(context=world.context()))

    assert update == {}
    assert world.crm.writes == 1
    assert len(world.notifier.sent) == 1


def test_a_lead_that_opts_out_before_the_decision_gets_no_side_effects(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)
    opt_out_lead(world.db, world.db.get(Lead, lead.id))
    world.db.commit()

    decide(world, lead, "approved")

    assert world.crm.writes == 0
    assert world.notifier.sent == []


def test_an_opted_out_lead_is_never_started(world: World) -> None:
    lead = add_lead(world.db, opt_out=True)

    with pytest.raises(ValueError, match="opted out"):
        start_lead(
            world.graph,
            world.context(),
            thread_id="t",
            lead_id=lead.id,
            campaign_id=world.campaign.id,
            rubric_id=world.rubrics["general-b2b"].id,
        )


def test_the_cached_enrichment_is_reused_on_a_second_run(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)
    requests_after_first = len(world.web.requests)

    start_lead(
        world.graph,
        world.context(),
        thread_id="second-run",
        lead_id=lead.id,
        campaign_id=world.campaign.id,
        rubric_id=world.rubrics["general-b2b"].id,
    )

    assert len(world.web.requests) == requests_after_first


def test_the_state_holds_plain_strings_not_enums(world: World) -> None:
    # Checkpoints must only hold plain data; LangGraph warns about (and will block) our classes.
    lead = add_lead(world.db)
    world.start(lead)

    state = decide(world, lead, "approved")

    assert type(state["route"]) is str
    assert type(state["decision"]) is str


def test_zero_point_hits_are_left_out_of_the_template(world: World) -> None:
    # A modern site with no reviews: web-agency's only hit is the 0-point category gate.
    lead = add_lead(world.db)

    state = world.start(lead, rubric="web-agency")

    assert state["score"] == 20  # copyright 2019 is older than 3 years
    assert state["score_explanation"] == "Score 20/100: copyright year older than 3 years (+20)."


def test_thread_status_follows_the_thread(world: World) -> None:
    lead = add_lead(world.db)
    thread = f"lead-{lead.id}"
    assert thread_status(world.graph, thread) == NEW

    world.start(lead)
    assert thread_status(world.graph, thread) == PAUSED

    decide(world, lead, "approved")
    assert thread_status(world.graph, thread) == FINISHED


def test_a_thread_whose_node_failed_continues_without_rerunning_finished_nodes(
    world: World,
) -> None:
    lead = add_lead(world.db)
    real_structured = world.llm.structured
    world.llm.structured = broken_llm
    with pytest.raises(RuntimeError):
        world.start(lead)
    thread = f"lead-{lead.id}"
    assert thread_status(world.graph, thread) == UNFINISHED
    requests_before = len(world.web.requests)

    world.llm.structured = real_structured
    state = continue_lead(world.graph, world.context(), thread)

    assert thread_status(world.graph, thread) == PAUSED
    assert len(world.web.requests) == requests_before  # enrich did not run again
    assert state["score"] == 60
    with pytest.raises(ValueError, match="nothing to continue"):
        continue_lead(world.graph, world.context(), thread)


def test_act_skips_the_message_when_the_run_item_says_it_was_sent(world: World) -> None:
    lead = add_lead(world.db)
    world.start(lead)
    decide(world, lead, "approved")
    state = current_state(world.graph, f"lead-{lead.id}")
    item = world.db.get(RunItem, state["run_item_id"])
    assert item.notified_at is not None

    # A crash right after the post: notified_at was committed, the checkpoint wasn't.
    update = act({**state, "notified": False}, Runtime(context=world.context()))

    assert "notified" not in update
    assert len(world.notifier.sent) == 1


def broken_llm(*_args, **_kwargs):
    raise RuntimeError("LLM down")
