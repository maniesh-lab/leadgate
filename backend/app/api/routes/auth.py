"""Logging in: `POST /api/auth/login` (email + password -> token), `GET /api/auth/me`,
and `POST /api/auth/password` (change your own password).

Login takes the standard OAuth2 password form (`username` = the email, `password`), so
the "Authorize" button in the OpenAPI docs works. The answer is a JWT the frontend
sends back as `Authorization: Bearer <token>` (see `app.api.deps.get_current_user`).

Guessing passwords is slowed down: every failed login is an `auth.login_failed` audit row
(with the caller's IP), and after MAX_FAILS_PER_EMAIL failures for one email, or
MAX_FAILS_PER_IP from one address, within LOGIN_WINDOW the answer is 429 until the
window has passed, even for the right password. The rows live in Postgres, so the limit
holds across API processes and restarts (no Redis).

A password change bumps the user's `token_version`, which ends every older token (other
browsers, a stolen token); the answer is a fresh token so this browser stays logged in.

The public demo's reviewer (DEMO_REVIEWER_EMAIL) is different: its password is on the
login page, so changing it is refused (403; it would lock every visitor out), and failed
logins for that email don't lock it (anyone could lock it for 15 minutes). The
per-address limit still applies.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CorrelationId, CurrentUser, DbSession, unauthorized
from app.api.errors import error_docs
from app.auth.security import create_access_token, dummy_hash, hash_password, verify_password
from app.config import Settings
from app.db.models import ActorType, AuditLog, User
from app.schemas.users import PasswordChange, Token, UserOut
from app.services.audit import record

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILS_PER_EMAIL = 5
# Higher than per email: a team behind one office address shares it.
MAX_FAILS_PER_IP = 20
LOGIN_FAILED = "auth.login_failed"


@router.post("/login", responses=error_docs(401, 429))
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> Token:
    """Swap an email + password for a login token (valid JWT_EXPIRE_MINUTES).

    429 after too many failed logins for this email or from this address (15 minutes).
    """
    email = form.username.strip().lower()[:254]
    # Behind nginx this is the browser's address: uvicorn reads X-Forwarded-For from
    # the proxies FORWARDED_ALLOW_IPS trusts (see docker-compose.yml and render.yaml).
    ip = request.client.host if request.client else "unknown"
    now = datetime.now(UTC)
    if too_many_failures(db, email, ip, now, lock_email=not is_demo(email, settings)):
        log.warning("login_rate_limited", email=email, ip=ip)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many failed logins; try again in 15 minutes",
            headers={"Retry-After": str(int(LOGIN_WINDOW.total_seconds()))},
        )

    user = db.scalars(select(User).where(func.lower(User.email) == email)).first()
    if user is None:
        verify_password(form.password, dummy_hash())  # same time as a wrong password
        ok = False
    else:
        ok = verify_password(form.password, user.password_hash) and user.is_active
    if not ok:
        log.warning("login_failed", email=email, ip=ip)
        row = record(
            db,
            actor_type=ActorType.USER,
            actor=email,
            action=LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id if user else None,
            details={"ip": ip},
            correlation_id=correlation_id,
        )
        row.occurred_at = now  # the same clock `too_many_failures()` compares with
        db.commit()
        # One message for every case, so it doesn't tell which emails exist.
        raise unauthorized("wrong email or password")

    token = make_token(user, settings, now)
    log.info("login", user_id=user.id)
    return Token(access_token=token)


@router.post("/password", responses=error_docs(400, 401, 403, 422))
def change_password(
    body: PasswordChange,
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> Token:
    """Change your own password (the current one is needed). Logs out your other browsers.

    The answer is a new token for this browser: the old one stops working. 403 for the
    public demo account.
    """
    if is_demo(user.email, settings):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "the demo account's password can't be changed"
        )
    # 400, not 401: a 401 would make the frontend log the user out.
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the current password is wrong")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    record(
        db,
        actor_type=ActorType.USER,
        actor=user.email,
        action="user.password_changed",
        entity_type="user",
        entity_id=user.id,
        correlation_id=correlation_id,
    )
    db.commit()
    log.info("password_changed", user_id=user.id)
    return Token(access_token=make_token(user, settings, datetime.now(UTC)))


@router.get("/me", responses=error_docs(401))
def me(user: CurrentUser) -> UserOut:
    """The logged-in user."""
    return UserOut.model_validate(user)


def too_many_failures(
    db: Session, email: str, ip: str, now: datetime, *, lock_email: bool = True
) -> bool:
    """True if this email (unless `lock_email` is off) or this address failed too often
    within LOGIN_WINDOW."""
    recent = select(func.count()).where(
        AuditLog.action == LOGIN_FAILED, AuditLog.occurred_at >= now - LOGIN_WINDOW
    )
    by_ip = db.scalar(recent.where(AuditLog.details["ip"].as_string() == ip)) or 0
    if by_ip >= MAX_FAILS_PER_IP:
        return True
    if not lock_email:
        return False
    by_email = db.scalar(recent.where(AuditLog.actor == email)) or 0
    return by_email >= MAX_FAILS_PER_EMAIL


def is_demo(email: str, settings: Settings) -> bool:
    """The public demo's reviewer (its login is on the login page)."""
    demo = settings.demo_reviewer_email
    return bool(demo) and email.strip().lower() == demo.strip().lower()


def make_token(user: User, settings: Settings, now: datetime) -> str:
    return create_access_token(
        user.id, settings.jwt_secret, settings.jwt_expire_minutes, now, user.token_version
    )
