"""Uploads (`/api/uploads/csv`): an admin uploads a CSV of leads in the dashboard.

The body is the file itself (`Content-Type: text/csv`), not a multipart form: the route
reads it in chunks and stops as soon as it passes `CSV_UPLOAD_MAX_BYTES` (413), so a huge
file is never held in memory or spooled to disk first. The file is stored in the
database (`csv_uploads`); a campaign then uses it with `source_options.csv_upload_id`.
Nobody sends a file path: the API never reads a file from the server's disk.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import AdminUser, AppSettings, DbSession
from app.api.errors import error_docs
from app.db.models import ActorType, CsvUpload
from app.schemas.uploads import CsvUploadOut
from app.services.audit import record
from app.services.uploads import BadUpload, parse_csv_upload

router = APIRouter(prefix="/uploads", tags=["uploads"], responses=error_docs(401, 403))


def too_large(cap: int) -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE, f"the file is bigger than the {cap:,}-byte limit"
    )


async def csv_body(request: Request, settings: AppSettings) -> bytes:
    """The request body, read up to the size limit (413 past it)."""
    cap = settings.csv_upload_max_bytes
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > cap:
        raise too_large(cap)  # refused before reading anything
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > cap:  # no (or a wrong) Content-Length: stop reading here
            raise too_large(cap)
    return bytes(data)


@router.post(
    "/csv",
    status_code=status.HTTP_201_CREATED,
    responses=error_docs(413, 422),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def upload_csv(
    # The admin check comes first, so a stranger's body is never read.
    admin: AdminUser,
    db: DbSession,
    data: Annotated[bytes, Depends(csv_body)],
    filename: Annotated[str, Query(max_length=255, description="the file's name")] = "",
) -> CsvUploadOut:
    """Store a CSV of leads (UTF-8, a header row, at most `CSV_UPLOAD_MAX_BYTES`)."""
    try:
        parsed = parse_csv_upload(data, filename)
    except BadUpload as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    upload = CsvUpload(
        filename=parsed.filename,
        content=parsed.text,
        size_bytes=len(data),
        row_count=parsed.row_count,
        columns=parsed.columns,
        uploaded_by=admin.id,
    )
    db.add(upload)
    db.flush()
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="csv.uploaded",
        entity_type="csv_upload",
        entity_id=upload.id,
        details={
            "filename": upload.filename,
            "size_bytes": upload.size_bytes,
            "row_count": upload.row_count,
        },
    )
    db.commit()
    return CsvUploadOut.model_validate(upload)
