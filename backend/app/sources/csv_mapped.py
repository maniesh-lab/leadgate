"""The `csv` source: leads from any CSV file, with a column mapping.

Lets a client bring their own list. The mapping says which CSV column fills which
`RawLead` field, e.g. `{"name": "Company", "phone": "Tel"}`. Without a mapping, columns
named like the fields (`name`, `phone`, ..., or `category`) are used.

Called by `app.services.ingestion` (a campaign with the `csv` source, using
`source_options["csv_path"]` / `["csv_mapping"]`) and by `python -m app.cli ingest`.

Bad rows (no name, a rating of 7, ...) are logged and skipped, never a crash.
Each row's `source_ref` is a hash of its name + phone + website, lightly normalized,
so the same row imported twice updates one lead instead of making two.
"""

import csv
import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from app.db.models import Campaign
from app.sources.base import LeadSource, RawLead, SourceError

# The RawLead fields a CSV column can fill.
FIELDS = (
    "name",
    "phone",
    "email",
    "website",
    "address",
    "lat",
    "lng",
    "category_hint",
    "rating",
    "review_count",
    "business_status",
)
# Without a mapping, a column with one of these names fills the field.
DEFAULT_COLUMN_NAMES = {field: field for field in FIELDS} | {"category_hint": "category"}
# Cell values that mean "no value".
EMPTY_VALUES = {"", "n/a", "na", "-", "none", "null"}


class CsvSource(LeadSource):
    name = "csv"

    def __init__(self, path: str | Path, mapping: dict[str, str] | None = None) -> None:
        super().__init__()
        self.path = Path(path)
        self.mapping = mapping

    def collect(self, campaign: Campaign | None = None) -> Iterator[RawLead]:
        """Yield one RawLead per good row. The campaign isn't needed: the file is the list."""
        self.skipped = 0
        if not self.path.is_file():
            raise SourceError(f"CSV file not found: {self.path}")

        # utf-8-sig drops the invisible marker Excel puts at the start of CSV files.
        with self.path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file, restkey="_extra_columns")
            mapping = resolve_mapping(reader.fieldnames or [], self.mapping)
            # Line 1 is the header, so data starts on line 2.
            for line, row in enumerate(reader, start=2):
                lead = self.row_to_lead(row, mapping, line)
                if lead is not None:
                    yield lead

    def row_to_lead(
        self, row: dict[str, str], mapping: dict[str, str], line: int
    ) -> RawLead | None:
        values = {field: clean_cell(row.get(column)) for field, column in mapping.items()}
        if all(value is None for value in values.values()):
            self.skip("empty row", line=line)
            return None
        if values.get("review_count"):
            values["review_count"] = values["review_count"].replace(",", "")  # "1,024" -> "1024"

        ref = row_hash(values.get("name"), values.get("phone"), values.get("website"))
        try:
            return RawLead(source=self.name, source_ref=ref, raw=row, **values)
        except ValidationError as exc:
            first = exc.errors()[0]
            self.skip(f"{first['loc'][0]}: {first['msg']}", line=line)
            return None


def resolve_mapping(header: list[str], mapping: dict[str, str] | None) -> dict[str, str]:
    """Return {field: column} for the columns this file has. Raises SourceError if it can't."""
    if mapping is None:
        by_lower_name = {column.strip().lower(): column for column in header}
        mapping = {
            field: by_lower_name[name]
            for field, name in DEFAULT_COLUMN_NAMES.items()
            if name in by_lower_name
        }

    unknown = set(mapping) - set(FIELDS)
    if unknown:
        raise SourceError(f"unknown fields in the mapping: {sorted(unknown)}; allowed: {FIELDS}")
    missing = [column for column in mapping.values() if column not in header]
    if missing:
        raise SourceError(f"columns not in the CSV header: {missing}")
    if "name" not in mapping:
        raise SourceError("the mapping needs a column for 'name'")
    return mapping


def clean_cell(value: str | None) -> str | None:
    if value is None or value.strip().lower() in EMPTY_VALUES:
        return None
    return value.strip()


def row_hash(name: str | None, phone: str | None, website: str | None) -> str:
    """sha1 of name|phone|website after light normalizing (case, spaces, phone punctuation)."""
    parts = [
        " ".join((name or "").lower().split()),
        re.sub(r"\D", "", phone or ""),
        normalize_website(website),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def normalize_website(website: str | None) -> str:
    """'HTTPS://www.Example.com/' -> 'example.com', so small spelling changes match."""
    text = (website or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.removeprefix("www.")
    return text.rstrip("/")
