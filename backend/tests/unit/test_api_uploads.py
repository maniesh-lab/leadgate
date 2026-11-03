"""Tests for `POST /api/uploads/csv` and campaigns that use an uploaded CSV."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import AuditLog, Campaign, CsvUpload, ScoringRubric
from app.services.ingestion import collect_campaign
from app.services.uploads import BadUpload, clean_filename, parse_csv_upload
from tests.unit.test_collect import fake_client

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
MESSY = (FIXTURES / "messy_dentists.csv").read_bytes()
MAPPING = json.loads((FIXTURES / "messy_dentists_mapping.json").read_text())


def upload(client: TestClient, headers: dict, data: bytes, filename: str = "leads.csv"):
    return client.post(
        "/api/uploads/csv",
        params={"filename": filename},
        content=data,
        headers={**headers, "Content-Type": "text/csv"},
    )


def test_admin_uploads_a_csv(client: TestClient, db: Session, as_admin, admin) -> None:
    data = b"\xef\xbb\xbfCompany,Tel\nSmile Co,512-555-0100\n\n"
    response = upload(client, as_admin, data)

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "leads.csv"
    assert body["columns"] == ["Company", "Tel"]  # Excel's marker dropped
    assert body["row_count"] == 1  # the blank line isn't a row
    assert body["uploaded_by"] == admin.id
    assert "content" not in body
    stored = db.get(CsvUpload, body["id"])
    assert stored is not None and stored.content.startswith("Company,Tel")
    [row] = db.scalars(select(AuditLog)).all()
    assert (row.action, row.entity_type, row.entity_id) == ("csv.uploaded", "csv_upload", stored.id)
    assert row.details == {"filename": "leads.csv", "size_bytes": len(data), "row_count": 1}


def test_only_admins_upload(client: TestClient, db: Session, as_reviewer) -> None:
    assert upload(client, as_reviewer, b"name\nx\n").status_code == 403
    assert upload(client, {}, b"name\nx\n").status_code == 401
    assert db.scalars(select(CsvUpload)).all() == []


def test_upload_size_cap(
    client: TestClient, settings: Settings, db: Session, as_admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "csv_upload_max_bytes", 20)
    assert upload(client, as_admin, b"name\n" + b"x" * 15).status_code == 201  # exactly 20

    too_big = upload(client, as_admin, b"name\n" + b"x" * 16)
    assert too_big.status_code == 413
    assert too_big.json()["error"]["message"] == "the file is bigger than the 20-byte limit"

    # No Content-Length (a streamed body): the route still stops at the cap.
    def chunks():
        yield b"name\n"
        yield b"x" * 100

    streamed = client.post(
        "/api/uploads/csv", content=chunks(), headers={**as_admin, "Content-Type": "text/csv"}
    )
    assert streamed.status_code == 413
    assert len(db.scalars(select(CsvUpload)).all()) == 1


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "the file is empty or has no header row"),
        (b"name,phone\n", "the file has a header but no rows"),
        (b"name\n\xff\xfe\n", "the file isn't UTF-8 text"),
        (b"PK\x03\x04\x00\x00", "the file isn't a CSV (it has binary data)"),
    ],
)
def test_bad_files_are_refused(client: TestClient, as_admin, data: bytes, message: str) -> None:
    response = upload(client, as_admin, data)
    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith(message)


def test_parse_keeps_only_the_file_name() -> None:
    assert clean_filename("C:\\Users\\me\\leads.csv") == "leads.csv"
    assert clean_filename("../../etc/passwd") == "passwd"
    assert clean_filename("") == "upload.csv"
    assert parse_csv_upload(b"name\nx\n", "/tmp/a.csv").filename == "a.csv"
    with pytest.raises(BadUpload):
        parse_csv_upload(b"\n\n", "a.csv")


def campaign_body(rubric: ScoringRubric, options: dict) -> dict:
    return {
        "name": "Client list",
        "niche": "dentist",
        "location": "Austin, TX",
        "sources": ["csv"],
        "source_options": options,
        "rubric_id": rubric.id,
    }


def test_campaign_from_an_upload_collects_its_rows(
    client: TestClient, db: Session, settings: Settings, rubric, as_admin
) -> None:
    upload_id = upload(client, as_admin, MESSY).json()["id"]
    bad_column = client.post(
        "/api/campaigns",
        json=campaign_body(rubric, {"csv_upload_id": upload_id, "csv_mapping": {"name": "Nope"}}),
        headers=as_admin,
    )
    assert bad_column.status_code == 422
    assert bad_column.json()["error"]["message"] == "columns not in leads.csv: Nope"

    response = client.post(
        "/api/campaigns",
        json=campaign_body(rubric, {"csv_upload_id": upload_id, "csv_mapping": MAPPING}),
        headers=as_admin,
    )
    assert response.status_code == 201
    campaign = db.get(Campaign, response.json()["id"])
    assert campaign is not None

    [outcome] = collect_campaign(db, campaign, settings, fake_client())
    assert outcome.error is None and outcome.result is not None
    assert outcome.result.new > 0


def test_patch_keeps_a_server_csv_path(client: TestClient, db: Session, rubric, as_admin) -> None:
    # The demo campaign reads a file in the image; an edit through the API must keep it.
    campaign = Campaign(
        name="demo",
        niche="dentist",
        location="Austin, TX",
        sources=["csv"],
        source_options={"csv_path": "demo/austin-dentists.csv"},
        rubric_id=rubric.id,
    )
    db.add(campaign)
    db.commit()

    response = client.patch(
        f"/api/campaigns/{campaign.id}",
        json={"source_options": {"country": "us"}},
        headers=as_admin,
    )
    assert response.status_code == 200
    assert response.json()["source_options"] == {
        "country": "us",
        "csv_path": "demo/austin-dentists.csv",
    }
