"""A small world for the agent tests: rubrics, a dentist campaign in Austin, leads, fakes.

`World` holds the DB session and every fake the graph touches (website, LLM, CRM,
notifier), plus one run that each started lead gets a run item in, so a test can run
the real graph with no network and then look at what happened (also in run_steps and
the audit log). The lead's website is `tests.fake_web.SMILE_SITE` (a reserved `.test` domain).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from app.agents.graph import build_graph
from app.agents.llm import LLM, FakeLLM
from app.agents.runner import start_lead
from app.agents.state import AgentContext, LeadState
from app.db.models import Campaign, Lead, Run, RunItem, RunTrigger, ScoringRubric
from app.db.seed import RUBRICS
from app.observability.tracing import Tracer
from app.services.crm import FakeCRM
from app.services.notify import FakeNotifier
from tests.fake_web import SMILE, SMILE_SITE, FakeWeb, make_crawler

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
AUSTIN = {
    "query": "Austin, TX",
    "country_code": "us",
    "display_name": "Austin, Travis County, Texas, United States",
}
_refs = count(1)


def add_lead(db: Session, name: str = "Smile Austin Dental", **fields) -> Lead:
    """An OSM-style dentist lead (override any column with keyword arguments)."""
    ref = f"node/{next(_refs)}"
    tags = {"name": name, "amenity": "dentist", "addr:city": "Austin", "addr:state": "TX"}
    values = {
        "source": "osm",
        "source_ref": ref,
        "source_key": f"osm:{ref}",
        "raw": {"type": "node", "tags": tags},
        "name": name,
        "phone": "(512) 555-0142",
        "website": SMILE,
        "website_domain": "smileaustin.test",
        "address": "4501 S Congress Ave, Austin, TX 78745",
        "category": "dentist",
    }
    lead = Lead(**(values | fields))
    db.add(lead)
    db.flush()
    return lead


@dataclass
class World:
    db: Session
    llm: LLM = field(default_factory=FakeLLM)
    web: FakeWeb = field(default_factory=lambda: FakeWeb(SMILE_SITE))
    crm: FakeCRM = field(default_factory=FakeCRM)
    notifier: FakeNotifier = field(default_factory=FakeNotifier)
    checkpointer: InMemorySaver = field(default_factory=InMemorySaver)
    tracer: Tracer = field(default_factory=Tracer)

    def __post_init__(self) -> None:
        self.rubrics = {}
        for name, rules in RUBRICS.items():
            rubric = ScoringRubric(name=name, version=1, rules=rules)
            self.db.add(rubric)
            self.rubrics[name] = rubric
        self.db.flush()  # gives the rubrics their ids
        self.campaign = Campaign(
            name="Austin dentists",
            niche="dentist",
            location="Austin, TX",
            sources=["osm"],
            source_options={"osm_area": AUSTIN},
            rubric_id=self.rubrics["general-b2b"].id,
        )
        self.db.add(self.campaign)
        self.db.flush()
        self.run = Run(
            campaign_id=self.campaign.id,
            trigger=RunTrigger.MANUAL,
            rubric_id=self.campaign.rubric_id,
        )
        self.db.add(self.run)
        self.db.commit()
        self.graph: CompiledStateGraph = build_graph(self.checkpointer)

    def context(self) -> AgentContext:
        return AgentContext(
            session=self.db,
            llm=self.llm,
            crawler=make_crawler(self.web),
            crm=self.crm,
            notifier=self.notifier,
            tracer=self.tracer,
            now=lambda: NOW,
        )

    def add_item(self, lead: Lead) -> RunItem:
        """The lead's run item in `self.run` (thread id `lead-<id>`)."""
        item = RunItem(run_id=self.run.id, lead_id=lead.id, thread_id=f"lead-{lead.id}")
        self.db.add(item)
        self.db.commit()
        return item

    def start(self, lead: Lead, rubric: str = "general-b2b") -> LeadState:
        item = self.add_item(lead)
        return start_lead(
            self.graph,
            self.context(),
            thread_id=item.thread_id,
            lead_id=lead.id,
            campaign_id=self.campaign.id,
            rubric_id=self.rubrics[rubric].id,
            run_item_id=item.id,
            correlation_id="test-correlation",
        )
