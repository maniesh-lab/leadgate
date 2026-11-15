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


class LoginAnswer(BaseModel):
    """The login's answer: a token, or (two-factor on) `two_factor_required` and a 5-minute
    `two_factor_token` to send with the authenticator code to `/api/auth/login/two-factor`."""

    access_token: str | None = None
    token_type: Literal["bearer"] = "bearer"
    two_factor_required: bool = False
    two_factor_token: str | None = None


class TwoFactorLogin(BaseModel):
    two_factor_token: str = Field(max_length=2048)
    code: str = Field(max_length=16)


class TwoFactorSetupStart(BaseModel):
    password: str = Field(max_length=128)


class TwoFactorSetup(BaseModel):
    """Shown once, to the user turning two-factor on: scan the QR code, or type the secret."""

    secret: str
    otpauth_uri: str
    qr_svg: str


class TwoFactorCode(BaseModel):
    code: str = Field(max_length=16)


class TwoFactorOff(BaseModel):
    password: str = Field(max_length=128)
    code: str = Field(max_length=16)


class ResetPassword(BaseModel):
    token: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class ResetLinkOut(BaseModel):
    """Shown once to the admin who made it. The dashboard builds the link from `token`."""

    token: str
    expires_at: datetime


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(FromRow):
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime
    two_factor_enabled: bool = False


class MeOut(UserOut):
    """`/api/auth/me`: also whether this admin must turn two-factor on before using the
    admin tools (REQUIRE_ADMIN_TWO_FACTOR)."""

    two_factor_needed: bool = False


class UserRow(UserOut):
    """A row of the users list: also when a login lock on the account ends."""

    locked_until: datetime | None = None


class AdminConfirm(BaseModel):
    """Your own password again (and a code from your app, if your two-factor is on), for a
    change a stolen login mustn't make: a new admin, or another admin's reset link or
    two-factor turned off."""

    password: str = Field(max_length=128)
    code: str | None = Field(default=None, max_length=16)


class UserCreate(BaseModel):
    email: str = Field(examples=["reviewer@example.com"])
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.REVIEWER
    confirm: AdminConfirm | None = Field(
        default=None, description="Needed for `role: admin` (see `AdminConfirm`)."
    )

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        return normalize_email(value)


class UserUpdate(BaseModel):
    """What an admin can change on an account: turn it off (`false`) or back on (`true`)."""

    is_active: bool
    confirm: AdminConfirm | None = Field(
        default=None, description="Needed to turn another admin back on (see `AdminConfirm`)."
    )
