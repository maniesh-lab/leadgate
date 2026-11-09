"""The run page's live stream (`GET /api/runs/{id}/events`, Server-Sent Events)."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes import runs
from app.db.models import Run, RunStatus
from tests.agent_world import World


def events(client: TestClient, run_id: int, headers: dict[str, str]) -> list[tuple[str, dict]]:
    """Read the stream to its end: [(event name, data), ...] (heartbeats skipped)."""
    found = []
    with client.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        name = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: ") and name:
                found.append((name, json.loads(line.removeprefix("data: "))))
                name = None
    return found


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(runs, "WAITING_POLL_SECONDS", 0.01)
    monkeypatch.setattr(runs, "STREAM_MAX_SECONDS", 0.3)


def test_a_finished_run_sends_its_detail_once_and_ends(
    client: TestClient, world: World, started, as_reviewer, db: Session
) -> None:
    run = db.get(Run, started["run"]["id"])
    run.status = RunStatus.COMPLETED
    db.commit()

    found = events(client, run.id, as_reviewer)

    assert [name for name, _ in found] == ["run"]
    detail = found[0][1]
    assert detail == client.get(f"/api/runs/{run.id}", headers=as_reviewer).json()


def test_a_waiting_run_sends_only_when_something_changes(
    client: TestClient, started, as_reviewer, fast
) -> None:
    # Nothing changes while it streams: one event, then it ends at the time limit.
    found = events(client, started["run"]["id"], as_reviewer)
    assert [name for name, _ in found] == ["run"]
    assert found[0][1]["status"] == "waiting_approval"


def test_a_deleted_run_says_gone(
    client: TestClient, started, as_reviewer, db: Session, monkeypatch, fast
) -> None:
    run_id = started["run"]["id"]
    calls = {"n": 0}
    real = runs.look_at_run

    def then_gone(factory, wanted, seen, login):
        calls["n"] += 1
        return ("gone", None, None) if calls["n"] > 1 else real(factory, wanted, seen, login)

    monkeypatch.setattr(runs, "look_at_run", then_gone)
    assert [name for name, _ in events(client, run_id, as_reviewer)] == ["run", "gone"]


def test_the_stream_needs_a_login_and_a_real_run(client: TestClient, started, as_reviewer) -> None:
    assert client.get(f"/api/runs/{started['run']['id']}/events").status_code == 401
    assert client.get("/api/runs/999/events", headers=as_reviewer).status_code == 404


def test_too_many_open_streams_get_busy(
    client: TestClient, started, as_reviewer, monkeypatch, fast
) -> None:
    monkeypatch.setattr(runs, "MAX_STREAMS_PER_USER", 0)
    assert [name for name, _ in events(client, started["run"]["id"], as_reviewer)] == ["busy"]
    assert runs._open_streams == {}  # nothing left counted


def test_a_stream_ends_when_the_login_stops_working(
    client: TestClient, started, as_reviewer, db: Session, reviewer
) -> None:
    factory = client.app.state.session_factory
    run_id = started["run"]["id"]
    assert runs.look_at_run(factory, run_id, None, (reviewer.id, 0))[0] == "ok"
    reviewer.token_version += 1  # e.g. a password change
    db.commit()
    assert runs.look_at_run(factory, run_id, None, (reviewer.id, 0))[0] == "logged_out"
    reviewer.is_active = False
    db.commit()
    assert runs.look_at_run(factory, run_id, None, (reviewer.id, 1))[0] == "logged_out"
