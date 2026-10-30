"""Bodies for `/api/rubrics`: a scoring rubric is a named, versioned list of rules.

A rule has the shape the rubric engine reads (`app.agents.scoring.rubric`):
`{"label", "field", "op", "value", "points", "required"}`. The checks here catch a
broken rule when it's saved, instead of when a lead is scored.
"""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.agents.scoring.rubric import OPS
from app.schemas.common import FromRow

Op = Literal["exists", "missing", "eq", "gte", "lte", "in", "contains", "older_than_years"]
assert set(Op.__args__) == set(OPS), "keep Op in step with the rubric engine's OPS"

NUMBER_OPS = {"gte", "lte", "older_than_years"}


class Rule(BaseModel):
    label: str = Field(min_length=1, max_length=100, examples=["has email"])
    # A lead fact ("email") or a website signal ("signals.copyright_year").
    field: str = Field(pattern=r"^[a-z_]+(\.[a-z_]+)?$", examples=["email"])
    op: Op
    value: Any = None
    points: int = Field(ge=-100, le=100)
    # A gate: if this rule misses, the score is 0 and the lead is flagged.
    required: bool = False

    @model_validator(mode="after")
    def check_value(self) -> Self:
        if self.op in NUMBER_OPS and (
            not isinstance(self.value, int | float) or isinstance(self.value, bool)
        ):
            raise ValueError(f"op {self.op!r} needs a number as its value")
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("op 'in' needs a list as its value")
        if self.op in {"eq", "contains"} and self.value is None:
            raise ValueError(f"op {self.op!r} needs a value")
        return self


class RubricCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9-]{1,50}$", examples=["web-agency"])
    rules: list[Rule] = Field(min_length=1, max_length=50)


class RubricEdit(BaseModel):
    """The new rules; they're saved as the next version of the rubric."""

    rules: list[Rule] = Field(min_length=1, max_length=50)


class RubricOut(FromRow):
    id: int
    name: str
    version: int
    rules: list[dict[str, Any]]
    created_by: int | None
    created_at: datetime
