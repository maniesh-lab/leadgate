"""Tests for the correlation-ID middleware and the JSON log lines it produces."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.observability.correlation import HEADER, pick_correlation_id


def test_keeps_a_valid_incoming_id(client: TestClient) -> None:
    response = client.get("/api/health", headers={HEADER: "n8n-run-42"})
    assert response.headers[HEADER] == "n8n-run-42"


def test_makes_a_new_id_when_missing(client: TestClient) -> None:
    response = client.get("/api/health")
    uuid.UUID(response.headers[HEADER])  # raises if it isn't a UUID


@pytest.mark.parametrize("bad", ["", "has space", "line\nbreak", "x" * 129])
def test_replaces_an_invalid_id(bad: str) -> None:
    uuid.UUID(pick_correlation_id(bad))


def test_request_log_line_is_json_with_correlation_id(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    # The app is built inside the test so the log handler writes to the captured stdout.
    with TestClient(create_app(settings)) as client:
        client.get("/api/health", headers={HEADER: "abc-123"})

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    request_lines = [line for line in lines if line.get("event") == "request"]
    assert request_lines, "no request log line found"
    assert request_lines[-1]["correlation_id"] == "abc-123"
    assert request_lines[-1]["path"] == "/api/health"
    assert request_lines[-1]["status"] == 200
    # Lines from outside a request still carry the key.
    assert all("correlation_id" in line for line in lines)
