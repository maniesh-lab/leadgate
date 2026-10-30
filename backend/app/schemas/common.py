"""Shapes several route areas share: a page of results, reading models from DB rows, and
a lead's review (its score breakdown)."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class FromRow(BaseModel):
    """A response model that can be built straight from a SQLAlchemy object (`model_validate`)."""

    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """One page of a list: `GET /api/leads?page=2&page_size=50` -> items 51-100 + the total."""

    items: list[T]
    total: int
    page: int
    page_size: int


class Review(BaseModel):
    """Why the lead scored what it did, and what extraction couldn't prove (from its steps)."""

    score_breakdown: list[dict[str, Any]] = []
    score_explanation: str | None = None
    extraction_issues: list[str] = []
