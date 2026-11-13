"""Logging in: `POST /api/auth/login` (email + password -> token, or a two-factor step),
`POST /api/auth/login/two-factor` (the authenticator code), `GET /api/auth/me`,
`POST /api/auth/password` (change your own password), `POST /api/auth/reset-password`
(a one-time link from an admin, app.services.accounts) and `/api/auth/two-factor/*`
(turn your own two-factor login on or off).

Login takes the standard OAuth2 password form (`username` = the email, `password`), so
the "Authorize" button in the OpenAPI docs works. The answer is a JWT the frontend
sends back as `Authorization: Bearer <token>` (see `app.api.deps.get_current_user`).

Guessing passwords is slowed down: every failed login is an `auth.login_failed` audit row
(with the caller's IP), and after MAX_FAILS_PER_EMAIL failures for one email, or
MAX_FAILS_PER_IP from one address, within LOGIN_WINDOW the answer is 429 until the
window has passed, even for the right password. The rows live in Postgres, so the limit
holds across API processes and restarts (no Redis). An admin's unlock (an `auth.unlocked`
row) makes the email's earlier failures stop counting.

Two-factor login: with it on, the right password answers `two_factor_required` and a
5-minute token that only `/login/two-factor` accepts (not a login: app.auth.security);
the authenticator code then gives the real token. A wrong code is a failed login like a
wrong password, so it counts toward the same lock, and each code works once (the user's
row is locked while it's checked, so two requests can't both use it). Turning two-factor
on or off needs the password (and to turn it off, a code), and ends the user's other
logins.

A password change bumps the user's `token_version`, which ends every older token (other
browsers, a stolen token); the answer is a fresh token so this browser stays logged in.

`confirm_yourself()` asks a logged-in admin for their password again (and a code, with
two-factor on) before a change a stolen login mustn't make: a new admin, or another
admin's reset link or two-factor off (`app.api.routes.users`). A wrong one counts as a
failed login (`check_password()`, `check_code()`).

The public demo's reviewer (DEMO_REVIEWER_EMAIL) is different: its password is on the
login page, so changing it is refused (403; it would lock every visitor out), and failed
logins for that email don't lock it (anyone could lock it for 15 minutes). The
per-address limit still applies.
"""

from datetime import UTC, datetime
from typing import Annotated

import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AppSettings,
    CorrelationId,
    CurrentUser,
    DbSession,
    needs_two_factor,
    unauthorized,
)
from app.api.errors import error_docs
from app.auth.security import (
    create_access_token,
    create_two_factor_token,
    dummy_hash,
    hash_password,
    new_totp_secret,
    qr_svg,
    read_two_factor_token,
    totp_step,
    totp_uri,
    verify_password,
)
from app.config import Settings
from app.db.models import ActorType, AuditLog, User
from app.schemas.users import (
    AdminConfirm,
    LoginAnswer,
    MeOut,
    PasswordChange,
    ResetPassword,
    Token,
    TwoFactorCode,
    TwoFactorLogin,
    TwoFactorOff,
    TwoFactorSetup,
    TwoFactorSetupStart,
)
from app.services.accounts import (
    LOGIN_FAILED,
    LOGIN_WINDOW,
    MAX_FAILS_PER_EMAIL,
    BadResetLink,
    Busy,
    counted_failures,
    one_attempt_at_a_time,
    use_reset_link,
)
from app.services.audit import record

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Higher than per email: a team behind one office address shares it.
MAX_FAILS_PER_IP = 20
TOO_MANY = "too many failed logins; try again in 15 minutes"


@router.post("/login", responses=error_docs(401, 429))
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> LoginAnswer:
    """Swap an email + password for a login token (valid JWT_EXPIRE_MINUTES), or, with
    two-factor on, for the token `/login/two-factor` takes with the authenticator code.

    429 after too many failed logins for this email or from this address (15 minutes).
    """
    email = form.username.strip().lower()[:254]
    # Behind nginx this is the browser's address: uvicorn reads X-Forwarded-For from
    # the proxies FORWARDED_ALLOW_IPS trusts (see docker-compose.yml and render.yaml).
    ip = request.client.host if request.client else "unknown"
    now = datetime.now(UTC)
    lock_email = not is_demo(email, settings)
    serialize(db, email, lock_email)
    if too_many_failures(db, email, ip, now, lock_email=lock_email):
        log.warning("login_rate_limited", email=email, ip=ip)
        raise too_many()

    user = db.scalars(select(User).where(func.lower(User.email) == email)).first()
    if user is None:
        verify_password(form.password, dummy_hash())  # same time as a wrong password
        ok = False
    else:
        ok = verify_password(form.password, user.password_hash) and user.is_active
    if not ok:
        record_failure(db, email, user, ip, now, correlation_id)
        # One message for every case, so it doesn't tell which emails exist.
        raise unauthorized("wrong email or password")

    if user.totp_enabled:
        step = create_two_factor_token(user.id, settings.jwt_secret, now, user.token_version)
        return LoginAnswer(two_factor_required=True, two_factor_token=step)
    log.info("login", user_id=user.id)
    return LoginAnswer(access_token=make_token(user, settings, now))


@router.post("/login/two-factor", responses=error_docs(401, 429))
def login_two_factor(
    body: TwoFactorLogin,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> Token:
    """The second step: the token from `/login` plus the code from the authenticator app.

    A wrong code counts as a failed login (429 after too many); each code works once.
    """
    expired = unauthorized("the login step has expired; log in again")
    try:
        claims = read_two_factor_token(body.two_factor_token, settings.jwt_secret)
    except jwt.InvalidTokenError as exc:
        raise expired from exc
    # Locked so two requests with the same code can't both pass (Postgres).
    user = db.get(User, claims.user_id, with_for_update=True)
    if (
        user is None
        or not user.is_active
        or not user.totp_enabled
        or not user.totp_secret
        or claims.version != user.token_version
    ):
        raise expired
    ip = request.client.host if request.client else "unknown"
    now = datetime.now(UTC)
    email = user.email.lower()
    if too_many_failures(db, email, ip, now, lock_email=not is_demo(email, settings)):
        raise too_many()
    step = totp_step(user.totp_secret, body.code, now)
    if step is None or step <= (user.totp_last_step or 0):
        record_failure(db, email, user, ip, now, correlation_id, two_factor=True)
        raise unauthorized("wrong code; check the time on your phone and try again")
    user.totp_last_step = step
    db.commit()
    log.info("login", user_id=user.id, two_factor=True)
    return Token(access_token=make_token(user, settings, now))


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT, responses=error_docs(400))
def reset_password(body: ResetPassword, db: DbSession) -> Response:
    """Set a new password with a one-time link an admin made. The link then stops working,
    and every older login of that user ends. 400 for a link that's unknown, used or
    expired (one message for all)."""
    try:
        user = use_reset_link(db, body.token, body.new_password, datetime.now(UTC))
    except BadResetLink as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "this link doesn't work (it was used, it expired, or it's wrong); "
            "ask an admin for a new one",
        ) from exc
    db.commit()
    log.info("password_reset", user_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/two-factor/setup", responses=error_docs(400, 401, 403, 409, 429))
def two_factor_setup(
    body: TwoFactorSetupStart,
    user: CurrentUser,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> TwoFactorSetup:
    """Start turning two-factor on: needs your password. Answers the secret and its QR code
    for an authenticator app; `/two-factor/enable` with a code from it finishes."""
    if is_demo(user.email, settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "the demo account can't use two-factor")
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is already on")
    check_password(db, user, body.password, request, settings, correlation_id)
    user.totp_secret = new_totp_secret()
    db.commit()
    uri = totp_uri(user.totp_secret, user.email)
    return TwoFactorSetup(secret=user.totp_secret, otpauth_uri=uri, qr_svg=qr_svg(uri))


@router.post("/two-factor/enable", responses=error_docs(400, 401, 409))
def two_factor_enable(
    body: TwoFactorCode,
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> Token:
    """Finish turning two-factor on with a code from the app. Ends your other logins; the
    answer is a new token for this browser."""
    if user.totp_enabled or not user.totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "start the setup first")
    now = datetime.now(UTC)
    step = totp_step(user.totp_secret, body.code, now)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong code; try the next one")
    user.totp_enabled = True
    user.totp_last_step = step
    user.token_version += 1
    audit_self(db, user, "user.two_factor_enabled", correlation_id)
    db.commit()
    return Token(access_token=make_token(user, settings, now))


@router.post("/two-factor/disable", responses=error_docs(400, 401, 409, 429))
def two_factor_disable(
    body: TwoFactorOff,
    user: CurrentUser,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    correlation_id: CorrelationId,
) -> Token:
    """Turn your two-factor login off: needs your password and a code. Ends your other
    logins; the answer is a new token for this browser."""
    if not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is off")
    check_password(db, user, body.password, request, settings, correlation_id)
    check_code(db, user, body.code, request, correlation_id)
    now = datetime.now(UTC)
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_last_step = None
    user.token_version += 1
    audit_self(db, user, "user.two_factor_disabled", correlation_id)
    db.commit()
    return Token(access_token=make_token(user, settings, now))


@router.post("/password", responses=error_docs(400, 401, 403, 422, 429))
def change_password(
    body: PasswordChange,
    user: CurrentUser,
    request: Request,
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
    check_password(db, user, body.current_password, request, settings, correlation_id)
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
def me(user: CurrentUser, settings: AppSettings) -> MeOut:
    """The logged-in user, and whether they must turn on two-factor login first."""
    answer = MeOut.model_validate(user)
    answer.two_factor_needed = needs_two_factor(user, settings)
    return answer


def serialize(db: Session, email: str, lock_email: bool) -> None:
    """One password check at a time for an email that can be locked (the demo's can't, so
    racing it gains nothing). 429 if another check for it takes too long."""
    if not lock_email:
        return
    try:
        one_attempt_at_a_time(db, email)
    except Busy as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "another login for this email is being checked; try again",
            headers={"Retry-After": "3"},
        ) from exc


def too_many() -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY,
        headers={"Retry-After": str(int(LOGIN_WINDOW.total_seconds()))},
    )


def record_failure(
    db: Session,
    email: str,
    user: User | None,
    ip: str,
    now: datetime,
    correlation_id: str | None,
    *,
    two_factor: bool = False,
) -> None:
    """An `auth.login_failed` row (wrong password or wrong code), committed at once."""
    log.warning("login_failed", email=email, ip=ip, two_factor=two_factor)
    details: dict = {"ip": ip}
    if two_factor:
        details["step"] = "two_factor"
    row = record(
        db,
        actor_type=ActorType.USER,
        actor=email,
        action=LOGIN_FAILED,
        entity_type="user",
        entity_id=user.id if user else None,
        details=details,
        correlation_id=correlation_id,
    )
    row.occurred_at = now  # the same clock `too_many_failures()` compares with
    db.commit()


def check_password(
    db: Session,
    user: User,
    password: str,
    request: Request,
    settings: Settings,
    correlation_id: str | None,
) -> None:
    """For a logged-in user re-entering their password (password change, two-factor on or
    off): 429 if their email or address is locked, else a wrong password is a failed login
    (so a stolen session can't be used to guess it) and a 400 (a 401 would log them out)."""
    ip = request.client.host if request.client else "unknown"
    now = datetime.now(UTC)
    email = user.email.lower()
    lock_email = not is_demo(email, settings)
    serialize(db, email, lock_email)
    if too_many_failures(db, email, ip, now, lock_email=lock_email):
        raise too_many()
    if not verify_password(password, user.password_hash):
        record_failure(db, email, user, ip, now, correlation_id)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the current password is wrong")


def check_code(
    db: Session, user: User, code: str, request: Request, correlation_id: str | None
) -> None:
    """A logged-in user's code from their app, good once: a wrong (or used) one is a failed
    login and a 400. Their row stays locked until the commit, so a code can't be used twice
    at once."""
    db.refresh(user, with_for_update=True)
    now = datetime.now(UTC)
    step = totp_step(user.totp_secret or "", code, now)
    if step is None or step <= (user.totp_last_step or 0):
        ip = request.client.host if request.client else "unknown"
        record_failure(db, user.email.lower(), user, ip, now, correlation_id, two_factor=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong code; try the next one")
    user.totp_last_step = step


def confirm_yourself(
    db: Session,
    user: User,
    confirm: AdminConfirm | None,
    request: Request,
    settings: Settings,
    correlation_id: str | None,
) -> None:
    """For a change a stolen login mustn't make (a new admin, another admin's reset link
    or two-factor): the user's own password again, and a code when their two-factor is on.
    A login token alone (say one read by an injected script) isn't enough then."""
    if confirm is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "re-enter your password to do this")
    check_password(db, user, confirm.password, request, settings, correlation_id)
    if user.totp_enabled:
        if not confirm.code:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter a code from your app too")
        check_code(db, user, confirm.code, request, correlation_id)


def audit_self(db: Session, user: User, action: str, correlation_id: str | None) -> None:
    record(
        db,
        actor_type=ActorType.USER,
        actor=user.email,
        action=action,
        entity_type="user",
        entity_id=user.id,
        correlation_id=correlation_id,
    )


def too_many_failures(
    db: Session, email: str, ip: str, now: datetime, *, lock_email: bool = True
) -> bool:
    """True if this email (unless `lock_email` is off) or this address failed too often
    within LOGIN_WINDOW. An email's failures before its latest unlock don't count."""
    recent = select(func.count()).where(
        AuditLog.action == LOGIN_FAILED, AuditLog.occurred_at >= now - LOGIN_WINDOW
    )
    by_ip = db.scalar(recent.where(AuditLog.details["ip"].as_string() == ip)) or 0
    if by_ip >= MAX_FAILS_PER_IP:
        return True
    if not lock_email:
        return False
    return len(counted_failures(db, email, now)) >= MAX_FAILS_PER_EMAIL


def is_demo(email: str, settings: Settings) -> bool:
    """The public demo's reviewer (its login is on the login page)."""
    demo = settings.demo_reviewer_email
    return bool(demo) and email.strip().lower() == demo.strip().lower()


def make_token(user: User, settings: Settings, now: datetime) -> str:
    return create_access_token(
        user.id, settings.jwt_secret, settings.jwt_expire_minutes, now, user.token_version
    )
