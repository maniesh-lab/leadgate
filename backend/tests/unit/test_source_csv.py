"""Tests for the `csv` source, on a hand-made messy CSV (tests/fixtures/csv/)."""

import json
from pathlib import Path

import pytest

from app.sources.base import SourceError
from app.sources.csv_mapped import CsvSource, normalize_website, row_hash

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
MESSY_CSV = FIXTURES / "messy_dentists.csv"
MAPPING = json.loads((FIXTURES / "messy_dentists_mapping.json").read_text())


def test_messy_csv_keeps_good_rows_and_skips_bad_ones() -> None:
    source = CsvSource(MESSY_CSV, MAPPING)
    leads = list(source.collect())

    # 10 data rows: no name, rating 7.5 and an all-empty row are skipped.
    assert len(leads) == 7
    assert source.skipped == 3
    assert "Downtown Dental Studio" not in [lead.name for lead in leads]


def test_row_values_are_trimmed_and_typed() -> None:
    leads = {lead.name: lead for lead in CsvSource(MESSY_CSV, MAPPING).collect()}

    smile = leads["Smile Dental Austin"]
    assert smile.phone == "(512) 555-0101"
    assert smile.rating == 4.6
    assert smile.review_count == 87
    assert smile.category_hint == "Dentist"
    assert smile.source_key.startswith("csv:")
    assert smile.raw["Company"] == "Smile Dental Austin"

    # "N/A", "-" and empty cells all mean "no value".
    barton = leads["Barton Creek Dental"]
    assert (barton.phone, barton.website, barton.rating, barton.review_count) == (None,) * 4

    assert leads["Mopac Orthodontics"].review_count == 1024


def test_same_business_spelled_differently_gets_the_same_key() -> None:
    leads = list(CsvSource(MESSY_CSV, MAPPING).collect())
    smiles = [lead for lead in leads if "smile dental" in lead.name.lower()]

    assert len(smiles) == 2
    assert smiles[0].source_key == smiles[1].source_key


def test_phone_with_and_without_country_code_are_different_keys() -> None:
    # Ingestion keeps both; the `check` node (Phase 5) flags them as possible duplicates.
    leads = [lead for lead in CsvSource(MESSY_CSV, MAPPING).collect() if "Lake" in lead.name]
    assert len({lead.source_key for lead in leads}) == 2


def test_without_mapping_columns_named_like_fields_are_used(tmp_path: Path) -> None:
    path = tmp_path / "plain.csv"
    path.write_text("Name,Phone,Category\nAcme Plumbing,512-555-0111,plumber\n")

    [lead] = CsvSource(path).collect()
    assert (lead.name, lead.phone, lead.category_hint) == (
        "Acme Plumbing",
        "512-555-0111",
        "plumber",
    )


def test_extra_cells_in_a_row_are_kept_in_raw(tmp_path: Path) -> None:
    path = tmp_path / "extra.csv"
    path.write_text("name\nAcme,surprise\n")

    [lead] = CsvSource(path).collect()
    assert lead.raw["_extra_columns"] == ["surprise"]


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ({"name": "Company", "fax": "Fax"}, "unknown fields"),
        ({"name": "Nope"}, "columns not in the CSV header"),
        ({"phone": "Tel"}, "needs a column for 'name'"),
    ],
)
def test_bad_mapping_raises(mapping: dict[str, str], message: str) -> None:
    with pytest.raises(SourceError, match=message):
        list(CsvSource(MESSY_CSV, mapping).collect())


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="not found"):
        list(CsvSource(tmp_path / "nope.csv").collect())


def test_normalize_website() -> None:
    assert normalize_website("HTTPS://www.Example.com/") == "example.com"
    assert normalize_website(None) == ""


def test_row_hash_ignores_case_spacing_and_phone_punctuation() -> None:
    assert row_hash("Acme  Co", "(512) 555-0111", "acme.example") == row_hash(
        "acme co", "512.555.0111", "https://www.acme.example/"
    )
