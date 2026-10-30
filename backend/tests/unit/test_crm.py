"""Tests for the CRM clients. No network: Airtable is answered by `httpx2.MockTransport`."""

import json

import httpx2
import pytest

from app.config import Settings
from app.services.crm import (
    RATE_LIMIT_WAIT_SECONDS,
    AirtableCRM,
    CRMError,
    FakeCRM,
    airtable_fields,
    make_crm,
)

FIELDS = {
    "name": "Smile Austin Dental",
    "phone": "+15125550142",
    "email": None,
    "website": "https://smile.test/",
    "address": "1 Main St",
    "city": "Austin",
    "category": "dentist",
    "score": 65,
    "score_explanation": "Good reviews and an old site.",
    "lead_id": 7,
    "run_item_id": 12,
}


class FakeAirtable:
    """Answers upserts like Airtable: the first write of a key creates, later ones update."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = list(statuses or [])  # answered first, one per request
        self.requests: list[httpx2.Request] = []
        self.ids: dict[str, str] = {}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.statuses:
            status = self.statuses.pop(0)
            return httpx2.Response(status, json={"error": {"type": "SOMETHING_WRONG"}})
        fields = json.loads(request.content)["records"][0]["fields"]
        key = fields["Thread ID"]
        created = key not in self.ids
        record_id = self.ids.setdefault(key, f"rec{len(self.ids) + 1:014d}")
        return httpx2.Response(
            200,
            json={
                "records": [{"id": record_id, "createdTime": "2026-10-30T12:00:00Z", "fields": {}}],
                "createdRecords": [record_id] if created else [],
                "updatedRecords": [] if created else [record_id],
            },
        )


def make_airtable(fake: FakeAirtable, waits: list[float] | None = None) -> AirtableCRM:
    client = httpx2.Client(transport=httpx2.MockTransport(fake))
    sleep = waits.append if waits is not None else (lambda _seconds: None)
    return AirtableCRM(client, "pat-test", "appTEST", "leadgate leads", sleep=sleep)


def test_upsert_sends_one_patch_matched_on_the_thread_id() -> None:
    fake = FakeAirtable()
    record_id = make_airtable(fake).upsert_lead("run-1-lead-7", FIELDS)

    assert record_id == "rec00000000000001"
    (request,) = fake.requests
    assert request.method == "PATCH"
    # The table name is URL-encoded (it has a space).
    assert str(request.url) == "https://api.airtable.com/v0/appTEST/leadgate%20leads"
    assert request.headers["Authorization"] == "Bearer pat-test"
    body = json.loads(request.content)
    assert body["performUpsert"] == {"fieldsToMergeOn": ["Thread ID"]}
    assert body["typecast"] is True
    fields = body["records"][0]["fields"]
    assert fields["Thread ID"] == "run-1-lead-7"
    assert fields["Name"] == "Smile Austin Dental"
    assert fields["Score explanation"] == "Good reviews and an old site."
    assert fields["Run item ID"] == 12
    assert "Email" not in fields  # empty values are left out


def test_writing_the_same_key_twice_updates_one_record() -> None:
    fake = FakeAirtable()
    crm = make_airtable(fake)

    first = crm.upsert_lead("run-1-lead-7", FIELDS)
    second = crm.upsert_lead("run-1-lead-7", {**FIELDS, "score": 70})

    assert first == second
    assert len(fake.ids) == 1


def test_a_rate_limit_waits_30_seconds_and_tries_again() -> None:
    fake = FakeAirtable(statuses=[429])
    waits: list[float] = []

    record_id = make_airtable(fake, waits).upsert_lead("run-1-lead-7", FIELDS)

    assert record_id.startswith("rec")
    assert waits == [RATE_LIMIT_WAIT_SECONDS]
    assert len(fake.requests) == 2


def test_a_second_rate_limit_gives_up() -> None:
    fake = FakeAirtable(statuses=[429, 429])
    with pytest.raises(CRMError, match="429"):
        make_airtable(fake).upsert_lead("run-1-lead-7", FIELDS)


def test_an_error_answer_says_what_airtable_said() -> None:
    fake = FakeAirtable(statuses=[422])
    with pytest.raises(CRMError, match="422.*SOMETHING_WRONG"):
        make_airtable(fake).upsert_lead("run-1-lead-7", FIELDS)
    assert len(fake.requests) == 1  # only a 429 is retried here; the worker retries the job


def test_a_network_error_is_a_crm_error_without_the_details() -> None:
    def fail(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    crm = AirtableCRM(
        httpx2.Client(transport=httpx2.MockTransport(fail)), "pat-test", "appTEST", "t"
    )
    with pytest.raises(CRMError, match="Airtable request failed: ConnectError"):
        crm.upsert_lead("run-1-lead-7", FIELDS)


def test_airtable_fields_skips_unknown_and_empty_fields() -> None:
    assert airtable_fields({"name": "A", "email": None, "something_else": 1}) == {"Name": "A"}


def test_make_crm_needs_all_three_settings(settings: Settings) -> None:
    client = httpx2.Client()
    assert isinstance(make_crm(settings, client), FakeCRM)

    partly = settings.model_copy(update={"airtable_token": "pat", "airtable_base_id": "app"})
    assert isinstance(make_crm(partly, client), FakeCRM)

    full = partly.model_copy(update={"airtable_table": "leadgate leads"})
    assert isinstance(make_crm(full, client), AirtableCRM)
