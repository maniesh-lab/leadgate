"""Tests for `collect_campaign()` and `python -m app.cli collect --campaign <id>`.

No network: OSM and Google answers come from the recorded / hand-written fixtures.
"""

import json
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

import app.cli
from app.cli import main
from app.config import Settings
from app.db.models import Campaign, Lead, ScoringRubric
from app.services.ingestion import collect_campaign

FIXTURES = Path(__file__).parents[1] / "fixtures"
CSV_OPTIONS = {
    "csv_path": str(FIXTURES / "csv" / "messy_dentists.csv"),
    "csv_mapping": json.loads((FIXTURES / "csv" / "messy_dentists_mapping.json").read_text()),
}


def fake_apis(request: httpx2.Request) -> httpx2.Response:
    """Answers Nominatim, Overpass and Google Text Search (first page only) from fixtures."""
    files = {
        "nominatim.openstreetmap.org": "osm/nominatim.json",
        "overpass-api.de": "osm/overpass.json",
        "places.googleapis.com": "google_places/text_search_page1.json",
    }
    answer = json.loads((FIXTURES / files[request.url.host]).read_text())
    if request.url.host == "places.googleapis.com":
        del answer["nextPageToken"]  # one page is enough here
    return httpx2.Response(200, json=answer)


def fake_client() -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(fake_apis))


def add_campaign(db: Session, sources: list[str], **options: object) -> Campaign:
    rubric = ScoringRubric(name="general-b2b", version=1, rules=[])
    db.add(rubric)
    db.flush()
    campaign = Campaign(
        name="Austin dentists",
        niche="dentist",
        location="Austin, TX",
        sources=sources,
        source_options={"max_results": 20, **options},
        rubric_id=rubric.id,
    )
    db.add(campaign)
    db.commit()
    return campaign


def lead_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Lead)) or 0


def test_osm_and_csv_then_a_second_run_adds_nothing(db: Session, settings: Settings) -> None:
    campaign = add_campaign(db, ["osm", "csv"], **CSV_OPTIONS)

    first = collect_campaign(db, campaign, settings, fake_client())
    assert [(o.source, o.result.new) for o in first if o.result] == [("osm", 20), ("csv", 6)]
    assert lead_count(db) == 26

    second = collect_campaign(db, campaign, settings, fake_client())
    assert [o.result.new for o in second if o.result] == [0, 0]
    assert lead_count(db) == 26

    # The geocode was saved on the campaign.
    db.expire_all()
    assert db.get(Campaign, campaign.id).source_options["osm_area"]["osm_id"] == 113314


def test_google_is_skipped_without_a_key(db: Session, settings: Settings) -> None:
    campaign = add_campaign(db, ["google_places"])

    [outcome] = collect_campaign(db, campaign, settings, fake_client())
    assert outcome.skipped == "GOOGLE_PLACES_API_KEY is not set"
    assert outcome.result is None and outcome.error is None


def test_google_runs_with_a_key(db: Session, settings: Settings) -> None:
    campaign = add_campaign(db, ["google_places"])
    settings = settings.model_copy(update={"google_places_api_key": "test-key"})

    [outcome] = collect_campaign(db, campaign, settings, fake_client())
    assert outcome.result is not None and outcome.result.new == 2


def test_a_failing_source_does_not_stop_the_others(db: Session, settings: Settings) -> None:
    campaign = add_campaign(db, ["csv", "nope", "osm"])  # csv without csv_path

    outcomes = collect_campaign(db, campaign, settings, fake_client())

    assert "needs source_options.csv_path" in (outcomes[0].error or "")
    assert "unknown source 'nope'" in (outcomes[1].error or "")
    assert outcomes[2].result is not None and outcomes[2].result.new == 20
    assert lead_count(db) == 20


def test_cli_collect(
    engine: Engine,
    db: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    campaign = add_campaign(db, ["osm", "google_places"])
    monkeypatch.setattr(app.cli, "make_http_client", lambda user_agent: fake_client())

    assert main(["collect", "--campaign", str(campaign.id)], settings) == 0
    out = capsys.readouterr().out
    assert "osm: 20 found, 20 new" in out
    assert "google_places: skipped (GOOGLE_PLACES_API_KEY is not set)" in out


def test_cli_collect_reports_failures(
    engine: Engine,
    db: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    campaign = add_campaign(db, ["csv"])
    monkeypatch.setattr(app.cli, "make_http_client", lambda user_agent: fake_client())

    assert main(["collect", "--campaign", str(campaign.id)], settings) == 1
    assert "csv: failed (the csv source needs source_options.csv_path)" in capsys.readouterr().out


def test_cli_collect_unknown_campaign(
    engine: Engine, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["collect", "--campaign", "999"], settings) == 1
    assert "no campaign with id 999" in capsys.readouterr().out
