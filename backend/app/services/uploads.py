"""CSV uploads: check a file an admin uploaded in the dashboard before it is stored.

`POST /api/uploads/csv` (`app.api.routes.uploads`) reads the body (at most
`CSV_UPLOAD_MAX_BYTES`) and calls `parse_csv_upload()`. A file that passes is saved in
`csv_uploads`; a campaign then points to it with `source_options["csv_upload_id"]`, and
the csv source (`app.sources.csv_mapped`) reads it from there when a run starts.

Only the shape is checked here (UTF-8 text, a header, at least one row). Each row's
fields are checked when a run collects them: bad rows are skipped then, as for any CSV.
"""

import csv
import io
from dataclasses import dataclass
from pathlib import PurePath


class BadUpload(ValueError):
    """The file can't be used as a CSV; the message says why (shown to the admin)."""


@dataclass
class ParsedUpload:
    filename: str
    text: str
    columns: list[str]
    row_count: int


def parse_csv_upload(data: bytes, filename: str) -> ParsedUpload:
    """Decode and check an uploaded CSV. Raises BadUpload."""
    try:
        # utf-8-sig drops the invisible marker Excel puts at the start of CSV files.
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadUpload("the file isn't UTF-8 text (in Excel: Save as → CSV UTF-8)") from exc
    if "\x00" in text:  # a binary file (e.g. an .xlsx); Postgres text can't hold it anyway
        raise BadUpload("the file isn't a CSV (it has binary data)")

    try:
        rows = csv.reader(io.StringIO(text, newline=""))
        header = next(rows, [])
        row_count = sum(1 for row in rows if any(cell.strip() for cell in row))
    except csv.Error as exc:
        raise BadUpload(f"the CSV can't be read: {exc}") from exc

    columns = [column.strip() for column in header]
    if not any(columns):
        raise BadUpload("the file is empty or has no header row")
    if row_count == 0:
        raise BadUpload("the file has a header but no rows")
    return ParsedUpload(
        filename=clean_filename(filename), text=text, columns=columns, row_count=row_count
    )


def clean_filename(filename: str) -> str:
    """Only the name, never a folder ("C:\\x\\leads.csv" -> "leads.csv"), at most 200 chars."""
    name = PurePath(filename.replace("\\", "/")).name.strip()
    return name[:200] or "upload.csv"
