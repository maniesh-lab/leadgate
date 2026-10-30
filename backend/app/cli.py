"""Command-line tools, run from backend/ as `python -m app.cli <command>`.

- `seed`: create the admin user (from ADMIN_EMAIL / ADMIN_PASSWORD) and the two rubrics
- `fake-data --leads N [--seed S]`: insert N fake leads (demos, SQL phase)
- `ingest <file> [--mapping mapping.json]`: import leads from any CSV file
- `collect --campaign <id>`: run the campaign's sources (osm, google_places, csv)
- `enrich --lead <id> | --campaign <id> | --stale [--limit N] [--force]`: crawl the
  leads' websites (`--stale`: every lead never enriched or past ENRICH_CACHE_DAYS)
- `opt-out --lead <id>`: wipe a lead's contact data and never collect or crawl it again
- `start-run --campaign <id>`: queue a run (the worker, `python -m app.worker`, does it)
- `decide --item <id> --decision approved|rejected [--reason ...] [--reviewer email]`:
  a reviewer's decision on a run item waiting at the approval gate (default reviewer:
  ADMIN_EMAIL); the worker then resumes the item
- `show-run --run <id>`: the run's status, stats and items, and any node that completed
  twice for one item (there should be none, even after the worker was killed)

Each command opens its own DB connection from DATABASE_URL. The real work lives in
other modules (`app.db.seed`, `app.db.fake_data`, `app.services.*`); this file only
parses arguments and prints results.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.fake_data import insert_fake_leads
from app.db.models import Campaign, Lead, Run, RunItem, RunStep, StepStatus, User
from app.db.seed import seed
from app.db.session import make_engine, make_session_factory
from app.enrichment.crawler import Crawler, make_crawler_client
from app.services.approvals import LeadOptedOut, NotAwaitingDecision, decide
from app.services.enrichment import CACHED, ENRICHED, campaign_leads, enrich_lead, stale_leads
from app.services.ingestion import collect_campaign, ingest_source
from app.services.privacy import opt_out_lead
from app.services.runs import create_run
from app.sources.base import SourceError, make_http_client
from app.sources.csv_mapped import CsvSource


def cmd_seed(settings: Settings, _args: argparse.Namespace) -> int:
    if not settings.admin_email or not settings.admin_password:
        print("ADMIN_EMAIL and ADMIN_PASSWORD must be set (in .env or the environment).")
        return 1

    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session:
        result = seed(session, settings.admin_email, settings.admin_password)
    engine.dispose()

    admin_note = "created" if result.admin_created else "already exists, left unchanged"
    print(f"admin {settings.admin_email}: {admin_note}")
    print(f"rubrics created: {', '.join(result.rubrics_created) or 'none (all exist)'}")
    return 0


def cmd_fake_data(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session:
        inserted = insert_fake_leads(session, args.leads, seed=args.seed)
    engine.dispose()
    print(f"inserted {inserted} fake leads")
    return 0


def cmd_ingest(settings: Settings, args: argparse.Namespace) -> int:
    mapping = None
    if args.mapping:
        try:
            mapping = json.loads(Path(args.mapping).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"can't read the mapping file: {exc}")
            return 1

    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session:
        try:
            result = ingest_source(session, CsvSource(args.file, mapping))
        except SourceError as exc:
            print(f"error: {exc}")
            return 1
        session.commit()
    engine.dispose()
    print(f"csv: {result.summary()}")
    return 0


def cmd_collect(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            campaign = session.get(Campaign, args.campaign)
            if campaign is None:
                print(f"no campaign with id {args.campaign}")
                return 1
            with make_http_client(settings.osm_user_agent) as client:
                outcomes = collect_campaign(session, campaign, settings, client)
    finally:
        engine.dispose()

    if not outcomes:
        print("the campaign lists no sources")
    for outcome in outcomes:
        if outcome.result is not None:
            print(f"{outcome.source}: {outcome.result.summary()}")
        elif outcome.skipped:
            print(f"{outcome.source}: skipped ({outcome.skipped})")
        else:
            print(f"{outcome.source}: failed ({outcome.error})")
    return 1 if any(outcome.error for outcome in outcomes) else 0


def make_crawler(settings: Settings, client: httpx2.Client) -> Crawler:
    """Tests replace this with a crawler on a mocked client and a fake clock."""
    return Crawler(client, settings.osm_user_agent, max_pages=settings.enrich_max_pages)


def leads_to_enrich(session: Session, settings: Settings, args: argparse.Namespace) -> list[Lead]:
    if args.lead is not None:
        lead = session.get(Lead, args.lead)
        return [lead] if lead else []
    if args.campaign is not None:
        return campaign_leads(session, args.campaign)
    return stale_leads(session, settings.enrich_cache_days, limit=args.limit)


def cmd_enrich(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url)
    counts: Counter[str] = Counter()
    try:
        with make_session_factory(engine)() as session:
            leads = leads_to_enrich(session, settings, args)
            if not leads:
                print("no leads to enrich")
                return 1 if args.lead is not None else 0
            with make_crawler_client(settings.osm_user_agent) as client:
                crawler = make_crawler(settings, client)
                for lead in leads:
                    outcome = enrich_lead(
                        session,
                        lead,
                        crawler,
                        cache_days=settings.enrich_cache_days,
                        force=args.force,
                    )
                    # One commit per lead: a crash later keeps the work done so far.
                    session.commit()
                    counts[outcome] += 1
                    print(describe_enrichment(lead, outcome))
    finally:
        engine.dispose()

    print("done: " + ", ".join(f"{count} {outcome}" for outcome, count in sorted(counts.items())))
    return 0


def describe_enrichment(lead: Lead, outcome: str) -> str:
    """One line per lead, e.g. 'lead 12 Smile Dental: enriched, 3/4 pages, 2 emails, ...'."""
    head = f"lead {lead.id} {lead.name}: {outcome}"
    if outcome == CACHED:
        return f"{head} (from {lead.enriched_at:%Y-%m-%d}; --force to crawl again)"
    if outcome != ENRICHED or lead.enrichment is None:
        return head
    found = lead.enrichment
    pages_ok = sum(page["error"] is None for page in found["pages"])
    signals = ", ".join(f"{name}={value}" for name, value in found["signals"].items())
    return (
        f"{head}, {pages_ok}/{len(found['pages'])} pages, "
        f"emails {found['emails'] or '-'}, phones {found['phones'] or '-'}, "
        f"socials {sorted(found['socials']) or '-'}\n    signals: {signals}"
    )


def cmd_opt_out(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            lead = session.get(Lead, args.lead)
            if lead is None:
                print(f"no lead with id {args.lead}")
                return 1
            opt_out_lead(session, lead)
            session.commit()
    finally:
        engine.dispose()
    print(f"lead {lead.id} {lead.name}: contact data and enrichment wiped, opted out")
    return 0


def cmd_start_run(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            campaign = session.get(Campaign, args.campaign)
            if campaign is None:
                print(f"no campaign with id {args.campaign}")
                return 1
            run = create_run(session, campaign, datetime.now(UTC))
    finally:
        engine.dispose()
    print(f"run {run.id} queued for campaign {campaign.id} ({campaign.name})")
    print("the worker (python -m app.worker) collects the leads and processes them")
    return 0


def cmd_decide(settings: Settings, args: argparse.Namespace) -> int:
    email = args.reviewer or settings.admin_email
    if not email:
        print("give --reviewer <email> (or set ADMIN_EMAIL)")
        return 1
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            reviewer = session.scalars(select(User).where(User.email == email)).first()
            if reviewer is None:
                print(f"no user {email}")
                return 1
            try:
                decide(session, args.item, reviewer, args.decision, args.reason, datetime.now(UTC))
            except (ValueError, LookupError, NotAwaitingDecision, LeadOptedOut) as exc:
                print(f"error: {exc}")
                return 1
    finally:
        engine.dispose()
    print(f"run item {args.item}: {args.decision} by {email}; the worker resumes it")
    return 0


def cmd_show_run(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            run = session.get(Run, args.run)
            if run is None:
                print(f"no run with id {args.run}")
                return 1
            print(describe_run(session, run))
    finally:
        engine.dispose()
    return 0


def describe_run(session: Session, run: Run) -> str:
    lines = [f"run {run.id}: {run.status.value} (campaign {run.campaign_id})"]
    lines.append(f"  stats: {json.dumps(run.stats, sort_keys=True)}")
    items = session.execute(
        select(RunItem, Lead.name)
        .join(Lead, Lead.id == RunItem.lead_id)
        .where(RunItem.run_id == run.id)
        .order_by(RunItem.id)
    )
    for item, name in items:
        route = f", route {item.route.value}" if item.route else ""
        flags = f", flags {item.flags}" if item.flags else ""
        error = f", error: {item.error}" if item.error else ""
        lines.append(
            f"  item {item.id} {name}: {item.status.value}, score {item.score}{route}{flags}{error}"
        )
    twice = session.execute(
        select(RunStep.run_item_id, RunStep.node, func.count())
        .join(RunItem, RunItem.id == RunStep.run_item_id)
        .where(RunItem.run_id == run.id, RunStep.status == StepStatus.COMPLETED)
        .group_by(RunStep.run_item_id, RunStep.node)
        .having(func.count() > 1)
    ).all()
    if twice:
        for item_id, node, count in twice:
            lines.append(f"  !! item {item_id}: {node} completed {count} times")
    else:
        lines.append("  no node completed twice for any item")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="leadgate tools")
    commands = parser.add_subparsers(dest="command", required=True)

    seed_parser = commands.add_parser("seed", help="create the admin user and the two rubrics")
    seed_parser.set_defaults(handler=cmd_seed)

    fake_parser = commands.add_parser("fake-data", help="insert fake leads for demos and tests")
    fake_parser.add_argument("--leads", type=int, required=True, help="how many leads to add")
    fake_parser.add_argument("--seed", type=int, help="random seed, for the same leads every time")
    fake_parser.set_defaults(handler=cmd_fake_data)

    ingest_parser = commands.add_parser("ingest", help="import leads from a CSV file")
    ingest_parser.add_argument("file", help="path to the CSV file")
    ingest_parser.add_argument(
        "--mapping", help='JSON file: {"name": "Company", "phone": "Tel", ...} (field -> column)'
    )
    ingest_parser.set_defaults(handler=cmd_ingest)

    collect_parser = commands.add_parser("collect", help="run a campaign's lead sources")
    collect_parser.add_argument("--campaign", type=int, required=True, help="campaign id")
    collect_parser.set_defaults(handler=cmd_collect)

    enrich_parser = commands.add_parser("enrich", help="crawl leads' websites for emails + signals")
    which = enrich_parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--lead", type=int, help="one lead id")
    which.add_argument("--campaign", type=int, help="the leads in this campaign's runs")
    which.add_argument(
        "--stale", action="store_true", help="every lead never enriched or past the cache age"
    )
    enrich_parser.add_argument("--limit", type=int, help="with --stale: at most this many leads")
    enrich_parser.add_argument(
        "--force", action="store_true", help="crawl again even when the cache is fresh"
    )
    enrich_parser.set_defaults(handler=cmd_enrich)

    opt_out_parser = commands.add_parser(
        "opt-out", help="wipe a lead's contact data and never collect or crawl it again"
    )
    opt_out_parser.add_argument("--lead", type=int, required=True, help="lead id")
    opt_out_parser.set_defaults(handler=cmd_opt_out)

    start_parser = commands.add_parser("start-run", help="queue a run of a campaign")
    start_parser.add_argument("--campaign", type=int, required=True, help="campaign id")
    start_parser.set_defaults(handler=cmd_start_run)

    decide_parser = commands.add_parser("decide", help="approve or reject a waiting run item")
    decide_parser.add_argument("--item", type=int, required=True, help="run item id")
    decide_parser.add_argument("--decision", choices=["approved", "rejected"], required=True)
    decide_parser.add_argument("--reason", help="why (required for a rejection)")
    decide_parser.add_argument("--reviewer", help="the reviewer's email (default: ADMIN_EMAIL)")
    decide_parser.set_defaults(handler=cmd_decide)

    show_parser = commands.add_parser("show-run", help="a run's status, items and steps")
    show_parser.add_argument("--run", type=int, required=True, help="run id")
    show_parser.set_defaults(handler=cmd_show_run)

    return parser


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(settings or get_settings(), args)


if __name__ == "__main__":
    sys.exit(main())
