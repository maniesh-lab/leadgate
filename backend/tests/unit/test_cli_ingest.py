"""Tests for `python -m app.cli ingest <file> [--mapping mapping.json]`."""

from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.cli import main
from app.config import Settings
from app.db.models import Lead

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
MESSY_CSV = str(FIXTURES / "messy_dentists.csv")
MAPPING = str(FIXTURES / "messy_dentists_mapping.json")


def test_ingest_messy_csv(
    engine: Engine, db: Session, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ingest", MESSY_CSV, "--mapping", MAPPING], settings) == 0

    assert "csv: 7 found, 6 new, 0 updated, 1 duplicates" in capsys.readouterr().out
    assert db.scalar(select(func.count()).select_from(Lead)) == 6


def test_ingest_without_name_column_fails_cleanly(
    engine: Engine, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    # The messy file's name column is "Company", so without a mapping there's no name.
    assert main(["ingest", MESSY_CSV], settings) == 1
    assert "needs a column for 'name'" in capsys.readouterr().out


def test_ingest_with_unreadable_mapping(
    engine: Engine, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "mapping.json"
    bad.write_text("{not json")
    assert main(["ingest", MESSY_CSV, "--mapping", str(bad)], settings) == 1
    assert "can't read the mapping file" in capsys.readouterr().out
