"""Manual check: run the OSM source for real on one small query. Needs internet.

Not a test: tests and CI never touch the network. Run it from backend/ by hand:

    python scripts/osm_check.py dentist "Austin, TX" --max 20
    python scripts/osm_check.py dentist "Austin, TX" --max 20 --save
    python scripts/osm_check.py dentist "Austin, TX" --max 20 --record tests/fixtures/osm

--save upserts the leads into DATABASE_URL (run it twice: the second run adds 0 new).
--record also writes the raw Nominatim and Overpass answers as JSON, to refresh the
test fixtures. Keep it to small queries: OSM's servers are shared and free.

Settings come from .env as usual, so DATABASE_URL must be set; for the compose DB:
    set -x DATABASE_URL postgresql+psycopg://leadgate:leadgate@localhost:5432/leadgate
"""

import argparse
import json
import sys
from pathlib import Path

import httpx2

from app.config import get_settings
from app.db.models import Campaign
from app.db.session import make_engine, make_session_factory
from app.services.ingestion import ingest
from app.sources.base import SourceError, make_http_client
from app.sources.osm import OsmSource


def recorder(folder: Path):  # noqa: ANN201 (a small httpx2 event hook)
    """An httpx2 response hook that saves each answer as <folder>/<service>.json."""
    folder.mkdir(parents=True, exist_ok=True)

    def save(response: httpx2.Response) -> None:
        response.read()
        name = "nominatim" if "nominatim" in response.request.url.host else "overpass"
        path = folder / f"{name}.json"
        path.write_text(json.dumps(response.json(), indent=2, ensure_ascii=False) + "\n")
        print(f"recorded {path}")

    return save


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OSM source for real (manual check).")
    parser.add_argument("niche", help='e.g. "dentist"')
    parser.add_argument("location", help='e.g. "Austin, TX"')
    parser.add_argument("--max", type=int, default=20, help="max businesses (default 20)")
    parser.add_argument("--save", action="store_true", help="upsert the leads into DATABASE_URL")
    parser.add_argument("--record", type=Path, help="folder to save the raw API answers in")
    args = parser.parse_args()

    settings = get_settings()
    client = make_http_client(settings.osm_user_agent)
    if args.record:
        client.event_hooks["response"] = [recorder(args.record)]
    # Not saved: just carries the niche, location and options the source reads.
    campaign = Campaign(
        name="osm check",
        niche=args.niche,
        location=args.location,
        source_options={"max_results": args.max},
    )

    source = OsmSource(client)
    try:
        leads = list(source.collect(campaign))
    except SourceError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        client.close()

    print(f"area: {campaign.source_options['osm_area']['display_name']}")
    for lead in leads:
        print(
            f"- {lead.name} | {lead.phone or '-'} | {lead.website or '-'} | {lead.address or '-'}"
        )
    with_phone = sum(1 for lead in leads if lead.phone)
    with_site = sum(1 for lead in leads if lead.website)
    print(f"{len(leads)} leads ({with_phone} with phone, {with_site} with website), ", end="")
    print(f"{source.skipped} skipped")

    if args.save:
        engine = make_engine(settings.database_url)
        with make_session_factory(engine)() as session:
            result = ingest(session, leads)
            session.commit()
        engine.dispose()
        print(f"saved: {result.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
