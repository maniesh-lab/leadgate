"""Bodies for `/api/auth/*` and `/api/users`."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.db.models import UserRole
from app.schemas.common import FromRow

# Good enough to catch typos; the real check is that the person can log in.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value: str) -> str:
    """Lowercase + trimmed, so "Ann@X.com " and "ann@x.com" are one user."""
    email = value.strip().lower()
    if not EMAIL.match(email):
        raise ValueError("not an email address")
    return email


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserOut(FromRow):
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    email: str = Field(examples=["reviewer@example.com"])
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.REVIEWER

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        return normalize_email(value)
