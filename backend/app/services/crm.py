"""Where approved leads go: the `CRMClient` interface, `AirtableCRM`, and `FakeCRM`.

The `act` node calls `upsert_lead(key, fields)`. The key is the lead's thread id
(one per run item), so calling it twice for the same item updates one record instead
of creating two: that's what makes the CRM write safe to repeat after a crash.

`make_crm(settings, client)` picks the implementation: `AirtableCRM` when the three
AIRTABLE_* settings are set, else `FakeCRM` (tests, CI, demos without an Airtable base).
Called by the worker (`app.worker.main.open_worker`).
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx2
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

AIRTABLE_API = "https://api.airtable.com/v0"
# The table's primary field. Airtable's upsert finds the record by it (`fieldsToMergeOn`).
KEY_FIELD = "Thread ID"
# Our field name → the column in the Airtable table (Boss created the table with these).
AIRTABLE_FIELDS = {
    "name": "Name",
    "phone": "Phone",
    "email": "Email",
    "website": "Website",
    "address": "Address",
    "city": "City",
    "category": "Category",
    "score": "Score",
    "score_explanation": "Score explanation",
    "lead_id": "Lead ID",
    "run_item_id": "Run item ID",
}
# Airtable's docs: after a 429, "wait 30 seconds before subsequent requests will succeed".
RATE_LIMIT_WAIT_SECONDS = 30
TRIES = 2


class CRMError(Exception):
    """The CRM write failed. The `act` node fails, and the worker retries the job."""


class CRMClient(ABC):
    @abstractmethod
    def upsert_lead(self, key: str, fields: dict[str, Any]) -> str:
        """Create the record with this key, or update it if it exists. Returns its id."""


class FakeCRM(CRMClient):
    """Keeps records in a dict. `writes` counts every call (tests check "exactly once")."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.ids: dict[str, str] = {}
        self.writes = 0

    def upsert_lead(self, key: str, fields: dict[str, Any]) -> str:
        self.writes += 1
        self.records[key] = fields
        return self.ids.setdefault(key, f"fake-crm-{len(self.ids) + 1}")


class AirtableCRM(CRMClient):
    """One Airtable table. Each lead is one `PATCH` upsert, matched on the Thread ID field.

    Airtable allows 5 requests per second per base; the worker writes one lead at a time,
    so that's never close. A 429 still waits 30 s and tries once more.
    """

    def __init__(
        self,
        client: httpx2.Client,
        token: str,
        base_id: str,
        table: str,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.token = token
        self.url = f"{AIRTABLE_API}/{base_id}/{quote(table, safe='')}"
        self.sleep = sleep  # tests pass a fake, so they don't wait 30 s

    def upsert_lead(self, key: str, fields: dict[str, Any]) -> str:
        body = {
            "performUpsert": {"fieldsToMergeOn": [KEY_FIELD]},
            # Lets Airtable turn our values into the cell types (e.g. a URL field).
            "typecast": True,
            "records": [{"fields": {KEY_FIELD: key, **airtable_fields(fields)}}],
        }
        answer = self.patch(body).json()
        record_id = answer["records"][0]["id"]
        created = record_id in answer.get("createdRecords", [])
        log.info("crm_written", key=key, record_id=record_id, created=created)
        return record_id

    def patch(self, body: dict[str, Any]) -> httpx2.Response:
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(1, TRIES + 1):
            try:
                response = self.client.patch(self.url, json=body, headers=headers)
            except httpx2.HTTPError as exc:
                raise CRMError(f"Airtable request failed: {type(exc).__name__}") from exc
            if response.status_code == 429 and attempt < TRIES:
                log.warning("airtable_rate_limited", wait_seconds=RATE_LIMIT_WAIT_SECONDS)
                self.sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            if response.is_error:
                # Airtable's error body says what's wrong (e.g. an unknown field name).
                raise CRMError(f"Airtable answered {response.status_code}: {response.text[:300]}")
            return response
        raise AssertionError("unreachable")  # the last try either returns or raises


def airtable_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Our fields under the table's column names. Empty values are left out."""
    return {
        AIRTABLE_FIELDS[name]: value
        for name, value in fields.items()
        if name in AIRTABLE_FIELDS and value is not None
    }


def make_crm(settings: Settings, client: httpx2.Client) -> CRMClient:
    """`AirtableCRM` when AIRTABLE_TOKEN, AIRTABLE_BASE_ID and AIRTABLE_TABLE are set."""
    if settings.airtable_token and settings.airtable_base_id and settings.airtable_table:
        log.info("crm_airtable", base_id=settings.airtable_base_id, table=settings.airtable_table)
        return AirtableCRM(
            client, settings.airtable_token, settings.airtable_base_id, settings.airtable_table
        )
    log.info("crm_fake", note="AIRTABLE_* not set: approved leads are kept in memory only")
    return FakeCRM()
