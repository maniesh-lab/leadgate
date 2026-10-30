"""Logging in: `POST /api/auth/login` (email + password -> token) and `GET /api/auth/me`.

Login takes the standard OAuth2 password form (`username` = the email, `password`), so
the "Authorize" button in the OpenAPI docs works. The answer is a JWT the frontend
sends back as `Authorization: Bearer <token>` (see `app.api.deps.get_current_user`).
"""

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select

from app.api.deps import AppSettings, CurrentUser, DbSession, unauthorized
from app.api.errors import error_docs
from app.auth.security import create_access_token, dummy_hash, verify_password
from app.db.models import User
from app.schemas.users import Token, UserOut

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", responses=error_docs(401))
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession, settings: AppSettings
) -> Token:
    """Swap an email + password for a login token (valid JWT_EXPIRE_MINUTES)."""
    email = form.username.strip().lower()
    user = db.scalars(select(User).where(func.lower(User.email) == email)).first()
    if user is None:
        verify_password(form.password, dummy_hash())  # same time as a wrong password
        ok = False
    else:
        ok = verify_password(form.password, user.password_hash) and user.is_active
    if not ok:
        log.warning("login_failed", email=email)
        # One message for every case, so it doesn't tell which emails exist.
        raise unauthorized("wrong email or password")

    now = datetime.now(UTC)
    token = create_access_token(user.id, settings.jwt_secret, settings.jwt_expire_minutes, now)
    log.info("login", user_id=user.id)
    return Token(access_token=token)


@router.get("/me", responses=error_docs(401))
def me(user: CurrentUser) -> UserOut:
    """The logged-in user."""
    return UserOut.model_validate(user)
