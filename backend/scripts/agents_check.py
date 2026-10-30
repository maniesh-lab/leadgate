"""Manual check: run the lead graph end to end on real leads with the real LLM. Needs internet.

Not a test: tests and CI never touch the network. Run it from backend/ by hand, after
Phase 3's `collect` filled the DB (the defaults are three Austin dentists from it):

    python scripts/agents_check.py --campaign 1 --leads 16 11 14
    python scripts/agents_check.py --campaign 1 --leads 16 --decision rejected

Each lead runs to the approval gate (website visit, Groq extraction, checks, score),
the card a reviewer would see is printed, then the script answers with --decision
(default: approved) and the graph routes and acts. The CRM and the notifier are the
fakes, so nothing is sent anywhere; the messages they would get are printed.

It writes to DATABASE_URL like the real thing would: enrichment, extracted fields,
score and status on each lead. The checkpoints stay in memory (Phase 6 makes them
durable). Settings come from .env; LLM_MODE is forced to groq unless --llm says other.
"""

import argparse
import json
import sys
import uuid

from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import build_graph
from app.agents.llm import SteadfastLLM, make_llm
from app.agents.runner import resume_lead, start_lead, waiting_for
from app.agents.state import AgentContext
from app.config import get_settings
from app.db.models import Campaign
from app.db.session import make_engine, make_session_factory
from app.enrichment.crawler import Crawler, make_crawler_client
from app.services.crm import FakeCRM
from app.services.notify import FakeNotifier


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the lead graph on real leads (manual).")
    parser.add_argument("--campaign", type=int, default=1, help="campaign id (default 1)")
    parser.add_argument("--leads", type=int, nargs="+", default=[16, 11, 14], help="lead ids")
    parser.add_argument("--decision", choices=["approved", "rejected"], default="approved")
    parser.add_argument("--llm", choices=["fake", "groq", "openai"], default="groq")
    args = parser.parse_args()

    settings = get_settings().model_copy(update={"llm_mode": args.llm})
    engine = make_engine(settings.database_url)
    llm = make_llm(settings)
    crm, notifier = FakeCRM(), FakeNotifier()
    graph = build_graph(InMemorySaver())
    try:
        with (
            make_session_factory(engine)() as session,
            make_crawler_client(settings.osm_user_agent) as client,
        ):
            campaign = session.get(Campaign, args.campaign)
            if campaign is None:
                print(f"no campaign with id {args.campaign}")
                return 1
            context = AgentContext(
                session=session,
                llm=llm,
                crawler=Crawler(
                    client, settings.osm_user_agent, max_pages=settings.enrich_max_pages
                ),
                crm=crm,
                notifier=notifier,
                enrich_cache_days=settings.enrich_cache_days,
            )
            for lead_id in args.leads:
                thread_id = f"check-{lead_id}-{uuid.uuid4().hex[:8]}"
                start_lead(
                    graph,
                    context,
                    thread_id=thread_id,
                    lead_id=lead_id,
                    campaign_id=campaign.id,
                    rubric_id=campaign.rubric_id,
                    correlation_id=thread_id,
                )
                card = waiting_for(graph, thread_id)
                print(f"\n=== lead {lead_id}: approval card ===")
                print(json.dumps(card, indent=2, ensure_ascii=False, default=str))
                reason = None if args.decision == "approved" else "manual check"
                state = resume_lead(
                    graph, context, thread_id, decision=args.decision, reason=reason
                )
                print(f"--> {args.decision}: route={state['route']}")
    finally:
        engine.dispose()
        llm.close()

    print("\n=== what the fakes received ===")
    print(f"CRM records: {json.dumps(crm.records, indent=2, ensure_ascii=False)}")
    for channel, text in notifier.sent:
        print(f"[{channel}] {text}")
    if isinstance(llm, SteadfastLLM):
        print(f"tokens used: {llm.client.total_usage.total_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
