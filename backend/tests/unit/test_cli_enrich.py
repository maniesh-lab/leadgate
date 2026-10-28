"""Tests for `python -m app.cli enrich` and `opt-out`.

No network: the crawler runs on `tests.fake_web`.
"""

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import app.cli
from app.cli import main
from app.config import Settings
from app.db.models import Lead
from app.db.session import make_session_factory
from tests.fake_web import SMILE, SMILE_SITE, FakeWeb, make_crawler


@pytest.fixture
def fake_web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    web = FakeWeb(SMILE_SITE)
    monkeypatch.setattr(app.cli, "make_crawler", lambda settings, client: make_crawler(web))
    return web


def add_lead(db: Session, ref: str, website: str | None) -> int:
    lead = Lead(source="osm", source_ref=ref, source_key=f"osm:{ref}", raw={}, name=f"Lead {ref}")
    lead.website = website
    db.add(lead)
    db.commit()
    return lead.id


def read_lead(engine: Engine, lead_id: int) -> Lead:
    with make_session_factory(engine)() as session:
        lead = session.get(Lead, lead_id)
        assert lead is not None
        return lead


def test_enrich_one_lead(
    db: Session,
    engine: Engine,
    settings: Settings,
    fake_web: FakeWeb,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lead_id = add_lead(db, "node/1", SMILE)

    assert main(["enrich", "--lead", str(lead_id)], settings) == 0
    out = capsys.readouterr().out
    assert f"lead {lead_id} Lead node/1: enriched, 3/4 pages" in out
    assert "info@smileaustin.test" in out and "copyright_year=2019" in out
    assert "done: 1 enriched" in out
    assert read_lead(engine, lead_id).enrichment["signals"]["https_and_mobile"] is True

    # Again: the cache answers, no new requests.
    requests_before = len(fake_web.requests)
    assert main(["enrich", "--lead", str(lead_id)], settings) == 0
    assert "cached (from " in capsys.readouterr().out
    assert len(fake_web.requests) == requests_before

    assert main(["enrich", "--lead", str(lead_id), "--force"], settings) == 0
    assert "enriched" in capsys.readouterr().out
    assert len(fake_web.requests) > requests_before


def test_enrich_unknown_lead(
    db: Session, settings: Settings, fake_web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["enrich", "--lead", "999"], settings) == 1
    assert "no leads to enrich" in capsys.readouterr().out


def test_enrich_stale_with_a_limit(
    db: Session,
    engine: Engine,
    settings: Settings,
    fake_web: FakeWeb,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = add_lead(db, "node/1", None)
    second = add_lead(db, "node/2", SMILE)

    assert main(["enrich", "--stale", "--limit", "1"], settings) == 0
    assert "done: 1 no_website" in capsys.readouterr().out
    assert read_lead(engine, first).enriched_at is not None
    assert read_lead(engine, second).enriched_at is None

    assert main(["enrich", "--stale"], settings) == 0
    assert "done: 1 enriched" in capsys.readouterr().out

    assert main(["enrich", "--stale"], settings) == 0
    assert "no leads to enrich" in capsys.readouterr().out


def test_enrich_campaign_without_runs(
    db: Session, settings: Settings, fake_web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["enrich", "--campaign", "1"], settings) == 0
    assert "no leads to enrich" in capsys.readouterr().out


def test_enrich_needs_a_target(db: Session, settings: Settings) -> None:
    with pytest.raises(SystemExit):
        main(["enrich"], settings)


def test_opt_out(
    db: Session,
    engine: Engine,
    settings: Settings,
    fake_web: FakeWeb,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lead_id = add_lead(db, "node/1", SMILE)

    assert main(["opt-out", "--lead", str(lead_id)], settings) == 0
    assert "opted out" in capsys.readouterr().out
    lead = read_lead(engine, lead_id)
    assert lead.opt_out is True and lead.website is None

    # An opted-out lead is never crawled, even when asked by id.
    assert main(["enrich", "--lead", str(lead_id), "--force"], settings) == 0
    assert "opted_out" in capsys.readouterr().out
    assert fake_web.requests == []


def test_opt_out_unknown_lead(
    db: Session, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["opt-out", "--lead", "999"], settings) == 1
    assert "no lead with id 999" in capsys.readouterr().out
