"""Bodies for `/api/campaigns`: what to look for (niche), where (location), with which
sources, scored by which rubric.

Checks that need the database or several fields at once (the rubric exists, OSM knows
the niche, csv has a path) live in `app.api.routes.campaigns.check_campaign()`, so a
create and an edit are checked the same way.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import FromRow

SourceName = Literal["osm", "google_places", "csv"]


def strip_text(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("must not be blank")
    return value.strip() if value is not None else None


def no_repeats(value: list[str] | None) -> list[str] | None:
    return list(dict.fromkeys(value)) if value is not None else None


class SourceOptions(BaseModel):
    """Settings for the sources. Unknown keys are refused, so a typo doesn't go unnoticed."""

    model_config = ConfigDict(extra="forbid")

    # osm + google_places: at most this many places per run (the sources' default is 50).
    max_results: int | None = Field(default=None, ge=1, le=500)
    # osm: search this far around the city's centre instead of inside its boundary.
    radius_km: float | None = Field(default=None, gt=0, le=50)
    # Two-letter country code for phone numbers when OSM's geocode doesn't give one.
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    # csv: a CSV file on the worker's disk, and which column holds which field.
    csv_path: str | None = None
    csv_mapping: dict[str, str] | None = None


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, examples=["Austin dentists"])
    niche: str = Field(min_length=1, max_length=100, examples=["dentist"])
    location: str = Field(min_length=1, max_length=200, examples=["Austin, TX"])
    sources: list[SourceName] = Field(min_length=1, examples=[["osm"]])
    source_options: SourceOptions = SourceOptions()
    rubric_id: int

    _strip = field_validator("name", "niche", "location")(strip_text)
    _no_repeats = field_validator("sources")(no_repeats)


class CampaignUpdate(BaseModel):
    """Only the fields sent are changed. `source_options`, when sent, replaces the old ones."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    niche: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, min_length=1, max_length=200)
    sources: list[SourceName] | None = Field(default=None, min_length=1)
    source_options: SourceOptions | None = None
    rubric_id: int | None = None
    is_archived: bool | None = None

    _strip = field_validator("name", "niche", "location")(strip_text)
    _no_repeats = field_validator("sources")(no_repeats)


class CampaignOut(FromRow):
    id: int
    name: str
    niche: str
    location: str
    sources: list[str]
    # Also holds `osm_area`, the place OSM's geocoder found (cached by the osm source).
    source_options: dict[str, Any]
    rubric_id: int
    created_by: int | None
    created_at: datetime
    is_archived: bool
