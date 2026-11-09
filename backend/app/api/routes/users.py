"""Users: an admin lists them, creates reviewers and turns accounts off or on (`/api/users`).

The first admin comes from `python -m app.cli seed`; every other account is made here.

Turning an account off (`PATCH /api/users/{id}` `{"is_active": false}`) ends its logins at
once: every request loads the user and refuses an inactive one, and the token version is
bumped too, so turning it back on doesn't bring its old tokens back. An admin can't turn
off their own account, and the last active admin can't be turned off (nobody could manage
users after that). Two admins turning each other off at once are serialized by locking
the active admins' rows, so one of them always stays.

Recovery (app.services.accounts): `POST /api/users/{id}/reset-link` makes a one-time link
for that person to pick a new password (the answer holds the token once, `no-store`, and
the audit row never does); `POST /api/users/{id}/unlock` lifts a login lock;
`POST /api/users/{id}/two-factor/off` clears someone else's two-factor login (a lost
phone) and ends their logins.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, AppSettings, DbSession, Paging, get_or_404, paginate
from app.api.errors import error_docs
from app.auth.security import hash_password
from app.db.models import ActorType, User, UserRole
from app.schemas.common import Page
from app.schemas.users import ResetLinkOut, UserCreate, UserOut, UserRow, UserUpdate
from app.services.accounts import (
    ResetLinkRefused,
    create_reset_link,
    locked_until,
    turn_off_two_factor,
    unlock,
)
from app.services.audit import record

router = APIRouter(prefix="/users", tags=["users"], responses=error_docs(401, 403))


@router.get("")
def list_users(_admin: AdminUser, db: DbSession, paging: Paging) -> Page[UserRow]:
    """Every user, oldest first, with when a login lock on them ends (if one is on)."""
    rows, total = paginate(db, select(User).order_by(User.id), paging)
    return Page(
        items=[user_row(db, user) for (user,) in rows],
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.post("", status_code=status.HTTP_201_CREATED, responses=error_docs(409))
def create_user(body: UserCreate, admin: AdminUser, db: DbSession) -> UserOut:
    """Add a user (a reviewer unless `role` says admin). 409 if the email is taken."""
    taken = HTTPException(status.HTTP_409_CONFLICT, f"a user with email {body.email} exists")
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email)) is not None:
        raise taken
    user = User(email=body.email, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:  # created by someone else a moment ago
        raise taken from exc
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email, "role": user.role.value},
    )
    db.commit()
    return UserOut.model_validate(user)


@router.patch("/{user_id}", responses=error_docs(404, 409))
def update_user(user_id: int, body: UserUpdate, admin: AdminUser, db: DbSession) -> UserOut:
    """Turn an account off (its logins end at once) or back on. 409 for your own account
    or the last active admin."""
    user = get_or_404(db, User, user_id, "user")
    if body.is_active == user.is_active:
        return UserOut.model_validate(user)  # nothing to change, nothing to audit
    if not body.is_active:
        if user.id == admin.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "you can't turn off your own account")
        if user.role == UserRole.ADMIN and active_admin_count(db) <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "the last active admin can't be turned off"
            )
        user.token_version += 1  # its tokens stay ended even if it's turned on again
    user.is_active = body.is_active
    record(
        db,
        actor_type=ActorType.USER,
        actor=admin.email,
        action="user.activated" if body.is_active else "user.deactivated",
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email, "role": user.role.value},
    )
    db.commit()
    return UserOut.model_validate(user)


def active_admin_count(db: Session) -> int:
    """How many admins are active, with their rows locked until the commit (Postgres)."""
    stmt = (
        select(User.id)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .order_by(User.id)
        .with_for_update()
    )
    return len(db.scalars(stmt).all())


@router.post("/{user_id}/reset-link", responses=error_docs(404, 409))
def make_reset_link(
    user_id: int, admin: AdminUser, db: DbSession, settings: AppSettings, response: Response
) -> ResetLinkOut:
    """A one-time link for this person to set a new password (30 minutes; a new link ends
    the old one). Send it to them yourself. 409 for your own account (use the Account
    page; locked out: the `reset-password` command), one that's off, or the demo's."""
    user = get_or_404(db, User, user_id, "user")
    if user.id == admin.id:
        # Otherwise a stolen login could set a new password without knowing the current one.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "change your own password on your Account page"
        )
    try:
        link = create_reset_link(
            db, user, datetime.now(UTC), by=admin, demo_email=settings.demo_reviewer_email
        )
    except ResetLinkRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    response.headers["Cache-Control"] = "no-store"  # the token must not be kept anywhere
    return ResetLinkOut(token=link.token, expires_at=link.expires_at)


@router.post("/{user_id}/unlock", responses=error_docs(404))
def unlock_user(user_id: int, admin: AdminUser, db: DbSession) -> UserRow:
    """Lift the lock on this person's login (after too many wrong passwords or codes)."""
    user = get_or_404(db, User, user_id, "user")
    unlock(db, user, datetime.now(UTC), by=admin, reason="admin")
    db.commit()
    return user_row(db, user)


@router.post("/{user_id}/two-factor/off", responses=error_docs(404, 409))
def two_factor_off(user_id: int, admin: AdminUser, db: DbSession) -> UserRow:
    """Turn off someone else's two-factor login (e.g. they lost their phone); their logins
    end. 409 for your own (use the Account page) or when it's already off."""
    user = get_or_404(db, User, user_id, "user")
    if user.id == admin.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "turn your own two-factor off on your Account page"
        )
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is already off")
    turn_off_two_factor(db, user, datetime.now(UTC), by=admin)
    db.commit()
    return user_row(db, user)


def user_row(db: Session, user: User) -> UserRow:
    row = UserRow.model_validate(user)
    row.locked_until = locked_until(db, user.email, datetime.now(UTC))
    return row
