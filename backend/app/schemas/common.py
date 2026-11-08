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


class RubricRef(BaseModel):
    """The rubric version a run scored with, and who it suits."""

    id: int
    name: str
    version: int
    description: str | None = None


class Review(BaseModel):
    """Why the lead scored what it did (its run's rubric and the `score` step), and what
    extraction couldn't prove (its `extract` step)."""

    rubric: RubricRef | None = None
    # From the `check` step: False when there was nothing to compare the category with (a
    # niche off the list, or a failed extraction); None before the check ran.
    category_checked: bool | None = None
    score_breakdown: list[dict[str, Any]] = []
    score_explanation: str | None = None
    extraction_issues: list[str] = []
