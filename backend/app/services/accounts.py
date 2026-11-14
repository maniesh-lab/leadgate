"""Account recovery: one-time password reset links, unlocking a locked login, and turning
someone's two-factor login off. Used by `app.api.routes.users`, `app.api.routes.auth` and
the CLI (`reset-password`, `unlock`); each writes an audit row and leaves the commit to
the caller.

**Reset links.** An admin (or the CLI, for a locked-out admin) makes a link; the person
opens it and picks their own password, so nobody else ever knows it. The link holds a
random token; only its SHA-256 is stored. It works once, for RESET_LINK_MINUTES, and a new
link for the same user ends the old ones. Using it ends the user's older login tokens
(`token_version`) and lifts a login lock on their email. The frontend puts the token after
`#` in the URL, so it never reaches a server log or a Referer header.

**Locks.** `app.api.routes.auth` locks an email after MAX_FAILS_PER_EMAIL failed logins in
LOGIN_WINDOW. The audit log is append-only, so unlocking can't delete those rows: it adds
an `auth.unlocked` row, and only failures after the latest one count. The per-address
limit isn't affected (it isn't tied to one account).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.security import hash_password, hash_reset_token, new_reset_token
from app.db.models import ActorType, AuditLog, PasswordResetLink, User
from app.services.audit import record

RESET_LINK_MINUTES = 30
LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILS_PER_EMAIL = 5
LOGIN_FAILED = "auth.login_failed"
UNLOCKED = "auth.unlocked"
# Who the CLI's audit rows name.
CLI_ACTOR = "cli"


class ResetLinkRefused(Exception):
    """The link can't be made for this user (the message says why)."""


class BadResetLink(Exception):
    """Unknown, used, expired, or for an account that's turned off. One message for all."""


@dataclass(frozen=True)
class ResetLink:
    token: str  # shown once; never stored or logged
    expires_at: datetime


def create_reset_link(
    db: Session, user: User, now: datetime, *, by: User | None, demo_email: str | None
) -> ResetLink:
    """A new one-time link for `user`; earlier unused links for them stop working."""
    if demo_email and user.email.lower() == demo_email.strip().lower():
        raise ResetLinkRefused("the demo account's password can't be changed")
    if not user.is_active:
        raise ResetLinkRefused("this account is turned off; turn it on first")
    db.execute(
        update(PasswordResetLink)
        .where(PasswordResetLink.user_id == user.id, PasswordResetLink.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_reset_token()
    expires_at = now + timedelta(minutes=RESET_LINK_MINUTES)
    db.add(
        PasswordResetLink(
            user_id=user.id,
            token_hash=hash_reset_token(token),
            created_by=by.id if by else None,
            created_at=now,
            expires_at=expires_at,
        )
    )
    audit(
        db,
        by,
        "user.reset_link_created",
        user,
        now,
        {"email": user.email, "expires_at": expires_at.isoformat()},
    )
    return ResetLink(token=token, expires_at=expires_at)


def use_reset_link(db: Session, token: str, new_password: str, now: datetime) -> User:
    """Set the password through a link. Raises BadResetLink (one message for every case)."""
    link = db.scalars(
        select(PasswordResetLink)
        .where(PasswordResetLink.token_hash == hash_reset_token(token))
        .with_for_update()
    ).first()
    if link is None or link.used_at is not None or link.expires_at <= now:
        raise BadResetLink
    user = db.get(User, link.user_id)
    if user is None or not user.is_active:
        raise BadResetLink
    link.used_at = now
    user.password_hash = hash_password(new_password)
    user.token_version += 1  # every older login ends
    details = {"email": user.email, "link_made_by": link.created_by}
    audit(db, None, "user.password_reset", user, now, details, actor=user.email)
    audit(db, None, UNLOCKED, user, now, unlock_details(user, "password reset"), actor=user.email)
    return user


def unlock(db: Session, user: User, now: datetime, *, by: User | None, reason: str) -> None:
    """Lift the failed-login lock on the user's email (adds an `auth.unlocked` row)."""
    audit(db, by, UNLOCKED, user, now, unlock_details(user, reason))


def unlock_details(user: User, reason: str) -> dict:
    return {"email": user.email.lower(), "reason": reason}


def turn_off_two_factor(db: Session, user: User, now: datetime, *, by: User | None) -> None:
    """Clear the user's two-factor login (e.g. a lost phone) and end their logins."""
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_last_step = None
    user.token_version += 1
    audit(db, by, "user.two_factor_turned_off", user, now, {"email": user.email})


def counted_failures(db: Session, email: str, now: datetime) -> list[datetime]:
    """When this email's failed logins happened, within LOGIN_WINDOW and after its latest
    unlock, oldest first."""
    since = now - LOGIN_WINDOW
    # Only an unlock inside the window matters; the bound lets the occurred_at index work.
    unlocked = db.scalar(
        select(func.max(AuditLog.occurred_at)).where(
            AuditLog.action == UNLOCKED,
            AuditLog.occurred_at > since,
            AuditLog.details["email"].as_string() == email,
        )
    )
    if unlocked is not None and unlocked > since:
        since = unlocked
    return list(
        db.scalars(
            select(AuditLog.occurred_at)
            .where(
                AuditLog.action == LOGIN_FAILED,
                AuditLog.actor == email,
                AuditLog.occurred_at > since,
            )
            .order_by(AuditLog.occurred_at)
        )
    )


class Busy(Exception):
    """Another password check for this email took longer than ATTEMPT_WAIT to finish."""


# How long a password check waits for another one for the same email (then: try again).
ATTEMPT_WAIT = "3s"


def one_attempt_at_a_time(db: Session, email: str) -> None:
    """Make password checks for this email (or `ip:<address>`) wait for each other until
    the transaction ends (Postgres advisory lock), so parallel guesses can't all pass the
    lock check before any failure is saved. A waiter gives up after ATTEMPT_WAIT (raises
    Busy) rather than holding a pooled connection. Elsewhere (SQLite in tests) it does
    nothing."""
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(text(f"SET LOCAL lock_timeout = '{ATTEMPT_WAIT}'"))  # a constant, not input
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:email))"), {"email": email})
    except OperationalError as exc:  # lock_timeout ran out
        db.rollback()
        raise Busy from exc
    # Only this lock gets the short wait; later locks in the transaction wait as usual.
    db.execute(text("SET LOCAL lock_timeout = DEFAULT"))


def locked_until(db: Session, email: str, now: datetime) -> datetime | None:
    """When the email's login lock ends, or None if it isn't locked."""
    failures = counted_failures(db, email.lower(), now)
    if len(failures) < MAX_FAILS_PER_EMAIL:
        return None
    # Locked until the oldest of the last MAX failures leaves the window.
    return failures[-MAX_FAILS_PER_EMAIL] + LOGIN_WINDOW


def audit(
    db: Session,
    by: User | None,
    action: str,
    user: User,
    now: datetime,
    details: dict,
    *,
    actor: str | None = None,
) -> None:
    """An audit row about `user`, made by `by` (an admin), `actor` (the user themself), or
    else the command line."""
    if by is not None:
        actor_type, name = ActorType.USER, by.email
    elif actor is not None:
        actor_type, name = ActorType.USER, actor
    else:
        actor_type, name = ActorType.SYSTEM, CLI_ACTOR
    row = record(
        db,
        actor_type=actor_type,
        actor=name,
        action=action,
        entity_type="user",
        entity_id=user.id,
        details=details,
    )
    row.occurred_at = now  # the clock the lock check compares with
