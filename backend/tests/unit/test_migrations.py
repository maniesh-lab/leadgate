"""The Alembic migration on SQLite: builds exactly the modelled tables and undoes cleanly."""

from datetime import UTC, datetime
from pathlib import Path

from alembic import command

from app.db.base import Base
from app.db.models import Lead, User, UserRole
from app.db.session import make_engine, make_session_factory
from tests.migrations import alembic_config, schema_diff, table_names


def test_upgrade_matches_models_and_downgrade_removes_everything(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = alembic_config(url)
    engine = make_engine(url)

    command.upgrade(config, "head")
    assert table_names(engine) == set(Base.metadata.tables) | {"alembic_version"}
    assert schema_diff(engine) == []

    command.downgrade(config, "base")
    assert table_names(engine) == {"alembic_version"}
    engine.dispose()


def test_migrated_db_fills_in_timestamp_defaults(tmp_path: Path) -> None:
    # schema_diff ignores server defaults, so check one works: SQLite has no now().
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)

    with make_session_factory(engine)() as session:
        user = User(email="a@example.com", password_hash="x", role=UserRole.ADMIN)
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.created_at.tzinfo is UTC
    engine.dispose()


def test_filler_websites_already_saved_are_blanked(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = alembic_config(url)
    command.upgrade(config, "4e8b2d6f1a93")  # the revision before
    engine = make_engine(url)
    found = {"website": "https://yourwebsite.com/", "emails": ["someone@yourwebsite.com"]}
    with make_session_factory(engine)() as session:
        for key, website, domain in [
            ("osm:node/1", "https://yourwebsite.com/bevel", "yourwebsite.com"),
            ("osm:node/2", "https://mangpahang.com/", "mangpahang.com"),
        ]:
            session.add(
                Lead(
                    source="osm",
                    source_ref=key.removeprefix("osm:"),
                    source_key=key,
                    raw={},
                    name=key,
                    website=website,
                    website_domain=domain,
                    enrichment=found,
                    enriched_at=datetime.now(UTC),
                )
            )
        session.commit()

    command.upgrade(config, "head")

    with make_session_factory(engine)() as session:
        filler, real = session.query(Lead).order_by(Lead.id).all()
        assert (filler.website, filler.website_domain) == (None, None)
        # What the crawl found came from the stranger's site.
        assert (filler.enrichment, filler.enriched_at) == (None, None)
        assert (real.website, real.enrichment) == ("https://mangpahang.com/", found)
    engine.dispose()
