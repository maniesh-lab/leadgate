"""The `n8n` source: leads sent in the body of the n8n webhook (`POST /api/webhooks/n8n/runs`).

Lets an n8n workflow bring its own list (e.g. rows from another tool) instead of having
leadgate search OSM. Each lead is a JSON object with the `RawLead` field names
(`name`, `phone`, `email`, `website`, `address`, `lat`, `lng`, `category`, `rating`,
`review_count`, `business_status`); other keys are kept in `raw` only.

The rows follow the same rules as a CSV file (`app.sources.csv_mapped`): bad rows are
skipped and counted, and `source_ref` is the same name|phone|website hash, so the same
lead sent twice updates one row. Numbers may come as JSON numbers or text.

Called by `app.services.ingestion.collect_inline()` (the worker's `start_run` job for a
run started with inline leads).
"""

from collections.abc import Iterator
from typing import Any

from app.db.models import Campaign
from app.sources.base import LeadSource, RawLead
from app.sources.csv_mapped import DEFAULT_COLUMN_NAMES, CsvSource


class InlineSource(CsvSource):
    name = "n8n"

    def __init__(self, leads: list[dict[str, Any]]) -> None:
        LeadSource.__init__(self)  # not CsvSource's: there's no file
        self.leads = leads

    def collect(self, campaign: Campaign | None = None) -> Iterator[RawLead]:
        self.skipped = 0
        for number, lead in enumerate(self.leads, start=1):
            row = {key: as_text(value) for key, value in lead.items()}
            mapping = {field: key for field, key in DEFAULT_COLUMN_NAMES.items() if key in row}
            # `line` in the skip log = the lead's position in the list (1 = first).
            result = self.row_to_lead(row, mapping, number)
            if result is not None:
                yield result


def as_text(value: Any) -> str | None:
    """The CSV rules read text: 4.5 -> "4.5"; None stays None."""
    return value if value is None or isinstance(value, str) else str(value)
