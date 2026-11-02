"""Answers for `/api/uploads/*`: what was stored, never the file itself."""

from datetime import datetime

from app.schemas.common import FromRow


class CsvUploadOut(FromRow):
    id: int
    filename: str
    size_bytes: int
    row_count: int
    # The header row: the column names a `csv_mapping` can point to.
    columns: list[str]
    uploaded_by: int | None
    created_at: datetime
