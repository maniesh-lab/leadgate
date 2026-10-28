"""Command-line tools, run from backend/ as `python -m app.cli <command>`.

- `seed`: create the admin user (from ADMIN_EMAIL / ADMIN_PASSWORD) and the two rubrics
- `fake-data --leads N [--seed S]`: insert N fake leads (demos, SQL phase)
- `ingest <file> [--mapping mapping.json]`: import leads from any CSV file
- `collect --campaign <id>`: run the campaign's sources (osm, google_places, csv)
- `enrich --lead <id> | --campaign <id> | --stale [--limit N] [--force]`: crawl the
  leads' websites (`--stale`: every lead never enriched or past ENRICH_CACHE_DAYS)
- `opt-out --lead <id>`: wipe a lead's contact data and never collect or crawl it again

Each command opens its own DB connection from DATABASE_URL. The real work lives in
other modules (`app.db.seed`, `app.db.fake_data`, `app.services.*`); this file only
parses arguments and prints results.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import httpx2
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.fake_data import insert_fake_leads
from app.db.models import Campaign, Lead
from app.db.seed import seed
from app.db.session import make_engine, make_session_factory
from app.enrichment.crawler import Crawler, make_crawler_client
from app.services.enrichment import CACHED, ENRICHED, campaign_leads, enrich_lead, stale_leads
from app.services.ingestion import collect_campaign, ingest_source
from app.services.privacy import opt_out_lead
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

    return parser


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(settings or get_settings(), args)


if __name__ == "__main__":
    sys.exit(main())
