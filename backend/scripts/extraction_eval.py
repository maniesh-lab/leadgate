"""Manual eval: how often does extraction get each field right? Needs the real LLM (internet).

Not a test: it calls Groq. Two files in `backend/eval/`, both kept in git:
- `extraction_leads.json`: what extraction sees for each lead (its source data, its website
  findings with the page text cut to what the prompt uses) and the campaign's place. So
  `run` needs no database, and cleaning up a database can't break it.
- `extraction_expected.json`: the right answers, each one checked against the lead's data.

Run it from backend/:

    python scripts/extraction_eval.py run
    python scripts/extraction_eval.py save --campaign 1 --limit 10
    python scripts/extraction_eval.py draft

`run`: extracts every lead that has an answer again and compares field by field. Exact
match (ignoring case) for email, area, city, country, category and established_year. The
answers list every real service on the site, and the LLM may name at most MAX_SERVICES,
so for services it reports precision (how many it named are real) and recall (how many it
found, out of MAX_SERVICES or the site's count, whichever is smaller).

`save` (needs DATABASE_URL): copies leads from a database into the leads file: the leads
given with --key, or else up to --limit leads of --source that have website
text and aren't in the file yet (all from the campaign's place: the database must hold
no other leads of that source with websites). It keeps the leads already there.

`draft`: adds an answer for every lead in the leads file that has none, from the LLM's
current answer, with `"checked": false` and an empty `"note"`. Someone then checks each
one against the lead's data, fixes it (saying why in `note`) and sets `"checked": true`.

Settings come from .env; LLM_MODE is forced to groq unless --llm says other.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agents.cleaning.lead import campaign_geo, lead_snapshot
from app.agents.llm import LLM, SteadfastLLM, make_llm
from app.agents.nodes.extract import HIDDEN_EMAIL, MAX_SERVICES, PROMPT_TEXT_CHARS, extract_lead
from app.config import Settings, get_settings
from app.db.models import Campaign, Lead
from app.db.session import make_engine, make_session_factory

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
LEADS_FILE = EVAL_DIR / "extraction_leads.json"
ANSWERS_FILE = EVAL_DIR / "extraction_expected.json"
EXACT_FIELDS = ("email", "area", "city", "country", "category", "established_year")
LEADS_NOTE = (
    "What extraction sees for each eval lead: the lead's OpenStreetMap data (© OpenStreetMap "
    "contributors, ODbL) and what leadgate's crawler found on the business's own public "
    "website, the page text cut to the part the prompt uses. Saved by "
    "`scripts/extraction_eval.py save`; `saved_at` says when each lead was copied."
)
HOW_TO_CHECK = (
    "For each case compare expected with the lead's data (OSM tags + its website text in "
    "extraction_leads.json), fix what is wrong, then set checked to true. Services: every "
    "treatment or service group the site lists, in the site's words, with no limit; no "
    "payment, insurance or marketing wording. A list inside services holds other wordings of "
    "one service (they count once). Any exact field may be a list of answers that all count. A "
    'founding year must be stated as one ("since 1998"), not a copyright or staff year.'
)


def read(path: Path, empty: dict[str, Any]) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else empty


def write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def extract(llm: LLM, place: dict[str, Any], lead: dict[str, Any]) -> dict[str, Any]:
    result = extract_lead(
        llm,
        niche=place["niche"],
        raw=lead["raw"],
        enrichment=lead["enrichment"],
        geo=place["geo"],
        year=datetime.now(UTC).year,
    )
    fields = {field: result["extracted"][field] for field in (*EXACT_FIELDS, "services")}
    return fields | {"extraction_ok": result["extraction_ok"]}


def saved_lead(lead: Lead, now: datetime) -> dict[str, Any]:
    """A lead as extraction sees it. The page text is cut the way the prompt cuts it, so the
    eval sends the model exactly what the app would."""
    enrichment = dict(lead.enrichment or {})
    if "text" in enrichment:
        enrichment["text"] = HIDDEN_EMAIL.sub("", enrichment["text"] or "")[:PROMPT_TEXT_CHARS]
    return {
        "source_key": lead.source_key,
        "name": lead.name,
        "saved_at": now.date().isoformat(),
        "raw": lead_snapshot(lead),
        "enrichment": enrichment,
    }


def save(args: argparse.Namespace) -> int:
    settings = get_settings()
    document = read(LEADS_FILE, {"note": LEADS_NOTE, "leads": []})
    known = {lead["source_key"] for lead in document["leads"]}
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            campaign = session.get(Campaign, args.campaign)
            if campaign is None:
                print(f"no campaign with id {args.campaign}")
                return 1
            place = {"niche": campaign.niche, "geo": campaign_geo(campaign)}
            if "place" in document and document["place"] != place:
                print(f"the file holds leads for {document['place']}, not {place}")
                return 1
            document["place"] = place
            query = select(Lead).where(Lead.opt_out.is_(False)).order_by(Lead.id)
            if args.key:
                query = query.where(Lead.source_key.in_(args.key))
            else:
                query = query.where(
                    Lead.source == args.source,
                    Lead.website.is_not(None),
                    Lead.source_key.not_in(known),
                    Lead.enrichment["text"].as_string() != "",
                ).limit(args.limit)
            now = datetime.now(UTC)
            saved = [saved_lead(lead, now) for lead in session.scalars(query)]
    finally:
        engine.dispose()
    by_key = {lead["source_key"]: lead for lead in document["leads"]}
    by_key.update({lead["source_key"]: lead for lead in saved})
    document["leads"] = list(by_key.values())
    write(LEADS_FILE, {"note": document["note"], "place": place, "leads": document["leads"]})
    for lead in saved:
        print(f"saved {lead['source_key']} {lead['name']}")
    print(f"{len(saved)} saved, {len(document['leads'])} leads in {LEADS_FILE.name}")
    return 0


def draft(llm: LLM, args: argparse.Namespace) -> int:
    leads = read(LEADS_FILE, {"leads": []})
    answers = read(ANSWERS_FILE, {"cases": []})
    answered = {case["source_key"] for case in answers["cases"]}
    for lead in leads["leads"]:
        if lead["source_key"] in answered:
            continue
        answer = extract(llm, leads["place"], lead)
        answer.pop("extraction_ok")
        answers["cases"].append(
            {
                "source_key": lead["source_key"],
                "name": lead["name"],
                "website": lead["raw"]["website"],
                "checked": False,
                "expected": answer,
                "note": "",
            }
        )
        print(f"drafted {lead['source_key']} {lead['name']}")
    write(
        ANSWERS_FILE,
        {
            "niche": leads["place"]["niche"],
            "location": leads["place"]["geo"]["location"],
            "how_to_check": HOW_TO_CHECK,
            "cases": answers["cases"],
        },
    )
    print("check each new case and set checked: true")
    return 0


def same(found: object, wanted: object) -> bool:
    """Exact match ignoring case; `wanted` may be a list of answers that all count."""
    if isinstance(wanted, list):
        return any(same(found, answer) for answer in wanted)
    if isinstance(found, str) and isinstance(wanted, str):
        return found.casefold() == wanted.casefold()
    return found == wanted


def name(service: str) -> str:
    return " ".join(service.casefold().split())


def service_groups(expected: list[str | list[str]]) -> list[set[str]]:
    """One set per real service: a list in the answers holds several wordings of one."""
    return [{name(s) for s in ([entry] if isinstance(entry, str) else entry)} for entry in expected]


def run(llm: LLM, args: argparse.Namespace) -> int:
    leads = read(LEADS_FILE, {"leads": []})
    by_key = {lead["source_key"]: lead for lead in leads["leads"]}
    answers = read(ANSWERS_FILE, {"cases": []})
    right = dict.fromkeys(EXACT_FIELDS, 0)
    services_listed = services_real = services_found = services_possible = 0
    ok_count = cases_run = 0
    for case in answers["cases"]:
        lead = by_key.get(case["source_key"])
        if lead is None:
            print(f"skip {case['source_key']}: not in {LEADS_FILE.name}")
            continue
        cases_run += 1
        answer = extract(llm, leads["place"], lead)
        expected = case["expected"]
        ok_count += answer["extraction_ok"]
        wrong = []
        for field in EXACT_FIELDS:
            if same(answer[field], expected[field]):
                right[field] += 1
            else:
                wrong.append(f"{field}: got {answer[field]!r}, want {expected[field]!r}")
        got = {name(service) for service in answer["services"]}
        groups = service_groups(expected["services"])
        real = got & set().union(*groups)
        services_listed += len(got)
        services_real += len(real)
        services_found += sum(bool(got & group) for group in groups)
        services_possible += min(len(groups), MAX_SERVICES)
        if extra := sorted(got - real):
            wrong.append(f"services not on the list: {extra}")
        flag = "" if case.get("checked") else " (not checked yet)"
        print(f"{case['source_key']} {case['name']}{flag}: {'; '.join(wrong) or 'all right'}")

    print(f"\n{cases_run} leads, extraction_ok on {ok_count}")
    for field, count in right.items():
        print(f"  {field:<17} {count}/{cases_run}")
    precision = services_real / services_listed if services_listed else 1.0
    recall = services_found / services_possible if services_possible else 1.0
    print(f"  services          precision {precision:.0%}, recall {recall:.0%}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extraction eval (manual, uses the real LLM).")
    parser.add_argument("mode", choices=["run", "save", "draft"])
    parser.add_argument("--campaign", type=int, help="save: the campaign giving niche + place")
    parser.add_argument("--key", nargs="*", help="save: these leads (source keys)")
    parser.add_argument("--source", default="osm", help="save: which source's leads")
    parser.add_argument("--limit", type=int, default=10, help="save: how many new leads")
    parser.add_argument("--llm", choices=["fake", "groq", "openai"], default="groq")
    args = parser.parse_args()

    if args.mode == "save":
        if args.campaign is None:
            parser.error("save needs --campaign")
        return save(args)

    # run and draft read only the two files: no database (DATABASE_URL may be unset).
    settings = Settings(database_url="sqlite://", llm_mode=args.llm)
    llm = make_llm(settings)
    try:
        code = draft(llm, args) if args.mode == "draft" else run(llm, args)
    finally:
        if isinstance(llm, SteadfastLLM):
            print(f"tokens used: {llm.client.total_usage.total_tokens}")
        llm.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
