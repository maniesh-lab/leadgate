"""The lead upsert on real Postgres (its `ON CONFLICT` path differs from SQLite's).

Skipped unless `TEST_POSTGRES_URL` is set (see `conftest.py` in this folder).
"""

import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.base import Base
from app.db.models import Lead
from app.db.session import make_engine, make_session_factory
from app.services.ingestion import ingest, ingest_source
from app.sources.base import RawLead
from app.sources.csv_mapped import CsvSource

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
MAPPING = json.loads((FIXTURES / "messy_dentists_mapping.json").read_text())


def test_upsert_on_postgres(postgres_url: str) -> None:
    engine = make_engine(postgres_url)
    Base.metadata.create_all(engine)

    with make_session_factory(engine)() as db:
        first = ingest_source(db, CsvSource(FIXTURES / "messy_dentists.csv", MAPPING))
        db.commit()
        second = ingest_source(db, CsvSource(FIXTURES / "messy_dentists.csv", MAPPING))
        db.commit()

        assert (first.new, first.duplicates, second.new, second.updated) == (6, 1, 0, 6)
        assert db.scalar(select(func.count()).select_from(Lead)) == 6

        # An empty value from the source keeps the stored one (COALESCE).
        lead = RawLead(source="osm", source_ref="node/9", raw={}, name="Acme", phone="1")
        ingest(db, [lead])
        ingest(db, [lead.model_copy(update={"phone": None, "name": "Acme Inc"})])
        db.commit()
        stored = db.scalars(select(Lead).where(Lead.source_key == "osm:node/9")).one()
        db.refresh(stored)
        assert (stored.name, stored.phone) == ("Acme Inc", "1")

    engine.dispose()
