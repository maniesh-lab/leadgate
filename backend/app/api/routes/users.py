"""Users: an admin lists them and creates reviewers (`/api/users`).

The first admin comes from `python -m app.cli seed`; every other account is made here.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, DbSession, Paging, paginate
from app.api.errors import error_docs
from app.auth.security import hash_password
from app.db.models import ActorType, User
from app.schemas.common import Page
from app.schemas.users import UserCreate, UserOut
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
