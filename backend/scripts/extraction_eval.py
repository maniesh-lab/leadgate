"""Manual eval: how often does extraction get each field right? Needs the real LLM (internet).

Not a test: it calls Groq, and it reads leads (with their website enrichment) from
DATABASE_URL. The right answers live in `backend/eval/extraction_expected.json`,
each one checked against the lead's own data. Run it from backend/:

    python scripts/extraction_eval.py run --campaign 1
    python scripts/extraction_eval.py draft --campaign 1 --source osm --limit 20

`run`: extracts every lead in the file again and compares field by field. Exact match
(ignoring case) for email, area, city, country, category and established_year; for
services it reports how many listed services are in the expected list (precision) and
how many expected ones were found (recall), since wording differs.

`draft`: makes a new answers file from the LLM's current answers, with
the lead's `website`, `"checked": false` and an empty `"note"` on every case. Someone
then checks each case against the lead's data, fixes it (saying why in `note`) and
sets `"checked": true`. It never overwrites a file that exists.

Settings come from .env; LLM_MODE is forced to groq unless --llm says other.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.cleaning.lead import campaign_geo, lead_snapshot
from app.agents.llm import LLM, SteadfastLLM, make_llm
from app.agents.nodes.extract import extract_lead
from app.config import get_settings
from app.db.models import Campaign, Lead
from app.db.session import make_engine, make_session_factory

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "eval" / "extraction_expected.json"
EXACT_FIELDS = ("email", "area", "city", "country", "category", "established_year")
HOW_TO_CHECK = (
    "For each case compare expected with the lead's data (OSM tags + its website), fix what is "
    "wrong, then set checked to true. Services: treatments or service groups the site lists, "
    "at most 10; no payment, insurance or marketing wording; no duplicates. A founding year "
    'must be stated as one ("since 1998"), not a copyright or staff year.'
)


def extract(llm: LLM, campaign: Campaign, lead: Lead) -> dict:
    result = extract_lead(
        llm,
        niche=campaign.niche,
        raw=lead_snapshot(lead),
        enrichment=lead.enrichment or {},
        geo=campaign_geo(campaign),
        year=datetime.now(UTC).year,
    )
    fields = {name: result["extracted"][name] for name in (*EXACT_FIELDS, "services")}
    return fields | {"extraction_ok": result["extraction_ok"]}


def draft(session: Session, llm: LLM, campaign: Campaign, args: argparse.Namespace) -> int:
    if args.file.exists():
        print(f"{args.file} exists; move it away first (draft never overwrites)")
        return 1
    leads = session.scalars(
        select(Lead)
        .where(Lead.source == args.source, Lead.opt_out.is_(False))
        .order_by(Lead.id)
        .limit(args.limit)
    )
    cases = []
    for lead in leads:
        answer = extract(llm, campaign, lead)
        answer.pop("extraction_ok")
        cases.append(
            {
                "source_key": lead.source_key,
                "name": lead.name,
                "website": lead.website,
                "checked": False,
                "expected": answer,
                "note": "",
            }
        )
        print(f"drafted {lead.source_key} {lead.name}")
    args.file.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "niche": campaign.niche,
        "location": campaign.location,
        "how_to_check": HOW_TO_CHECK,
        "cases": cases,
    }
    args.file.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(cases)} cases to {args.file}; check each one and set checked: true")
    return 0


def same(found: object, wanted: object) -> bool:
    if isinstance(found, str) and isinstance(wanted, str):
        return found.casefold() == wanted.casefold()
    return found == wanted


def run(session: Session, llm: LLM, campaign: Campaign, args: argparse.Namespace) -> int:
    document = json.loads(args.file.read_text())
    right = dict.fromkeys(EXACT_FIELDS, 0)
    services_found = services_listed = services_expected = services_hit = 0
    ok_count = cases_run = 0
    for case in document["cases"]:
        lead = session.scalars(select(Lead).where(Lead.source_key == case["source_key"])).first()
        if lead is None:
            print(f"skip {case['source_key']}: not in this DB")
            continue
        cases_run += 1
        answer = extract(llm, campaign, lead)
        expected = case["expected"]
        ok_count += answer["extraction_ok"]
        wrong = []
        for name in EXACT_FIELDS:
            if same(answer[name], expected[name]):
                right[name] += 1
            else:
                wrong.append(f"{name}: got {answer[name]!r}, want {expected[name]!r}")
        got = {service.casefold() for service in answer["services"]}
        want = {service.casefold() for service in expected["services"]}
        services_listed += len(got)
        services_found += len(got & want)
        services_expected += len(want)
        services_hit += len(want & got)
        flag = "" if case.get("checked") else " (not checked yet)"
        print(f"{case['source_key']} {case['name']}{flag}: {'; '.join(wrong) or 'all right'}")

    print(f"\n{cases_run} leads, extraction_ok on {ok_count}")
    for name, count in right.items():
        print(f"  {name:<17} {count}/{cases_run}")
    precision = services_found / services_listed if services_listed else 1.0
    recall = services_hit / services_expected if services_expected else 1.0
    print(f"  services          precision {precision:.0%}, recall {recall:.0%}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extraction eval (manual, uses the real LLM).")
    parser.add_argument("mode", choices=["run", "draft"])
    parser.add_argument("--campaign", type=int, default=1, help="gives the niche and the place")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="the answers file")
    parser.add_argument("--source", default="osm", help="draft: which source's leads")
    parser.add_argument("--limit", type=int, default=20, help="draft: how many leads")
    parser.add_argument("--llm", choices=["fake", "groq", "openai"], default="groq")
    args = parser.parse_args()

    settings = get_settings().model_copy(update={"llm_mode": args.llm})
    engine = make_engine(settings.database_url)
    llm = make_llm(settings)
    try:
        with make_session_factory(engine)() as session:
            campaign = session.get(Campaign, args.campaign)
            if campaign is None:
                print(f"no campaign with id {args.campaign}")
                return 1
            handler = draft if args.mode == "draft" else run
            code = handler(session, llm, campaign, args)
    finally:
        engine.dispose()
    if isinstance(llm, SteadfastLLM):
        print(f"tokens used: {llm.client.total_usage.total_tokens}")
    llm.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
