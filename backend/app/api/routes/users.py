"""Users: an admin lists them, creates reviewers and turns accounts off or on (`/api/users`).

The first admin comes from `python -m app.cli seed`; every other account is made here.

Turning an account off (`PATCH /api/users/{id}` `{"is_active": false}`) ends its logins at
once: every request loads the user and refuses an inactive one, and the token version is
bumped too, so turning it back on doesn't bring its old tokens back. An admin can't turn
off their own account, and the last active admin can't be turned off (nobody could manage
users after that). Two admins turning each other off at once are serialized by locking
the active admins' rows, so one of them always stays.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, DbSession, Paging, get_or_404, paginate
from app.api.errors import error_docs
from app.auth.security import hash_password
from app.db.models import ActorType, User, UserRole
from app.schemas.common import Page
from app.schemas.users import UserCreate, UserOut, UserUpdate
from app.services.audit import record

router = APIRouter(prefix="/users", tags=["users"], responses=error_docs(401, 403))


@router.get("")
def list_users(_admin: AdminUser, db: DbSession, paging: Paging) -> Page[UserOut]:
    """Every user, oldest first."""
    rows, total = paginate(db, select(User).order_by(User.id), paging)
    return Page(
        items=[UserOut.model_validate(user) for (user,) in rows],
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
