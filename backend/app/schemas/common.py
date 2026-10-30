"""Shapes several route areas share: a page of results, and reading models from DB rows."""

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
