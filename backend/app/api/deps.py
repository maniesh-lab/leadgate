"""FastAPI dependencies every route shares: the DB session, the logged-in user, roles, paging.

- `DbSession`: a session for this request, closed (and rolled back if not committed)
  when the request ends. Routes commit themselves.
- `CurrentUser`: the user whose token came in `Authorization: Bearer <token>`; 401 if
  the token is missing, bad or expired, or the user is gone or deactivated.
- `require_role(...)`: 403 unless the user has one of the roles. `AdminUser` and
  `ReviewerUser` are the two the routes use (an admin can do anything a reviewer can).
- `PageParams` + `paginate()`: `?page=&page_size=` and the `{items, total, page,
  page_size}` answer.

`create_app()` puts the settings and the session factory on `app.state`; these read them.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.auth.security import read_access_token
from app.config import Settings
from app.db.models import User, UserRole

# Where the docs' "Authorize" button sends the email + password.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_correlation_id(request: Request) -> str:
    """The request's id (set by the correlation middleware), passed on to runs and jobs."""
    return request.state.correlation_id


AppSettings = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[Session, Depends(get_db)]
CorrelationId = Annotated[str, Depends(get_correlation_id)]


def unauthorized(message: str) -> HTTPException:
    # The WWW-Authenticate header is what the OAuth2 standard asks for on a 401.
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, message, headers={"WWW-Authenticate": "Bearer"}
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: DbSession, settings: AppSettings
) -> User:
    try:
        user_id = read_access_token(token, settings.jwt_secret)
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("the login has expired; log in again") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("invalid login token") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized("this user no longer exists or is deactivated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole) -> Callable[[User], User]:
    """A dependency that lets only users with one of `roles` through (else 403)."""

    def check(user: CurrentUser) -> User:
        if user.role not in roles:
            allowed = " or ".join(role.value for role in roles)
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"only an {allowed} can do this")
        return user

    return check


AdminUser = Annotated[User, Depends(require_role(UserRole.ADMIN))]
ReviewerUser = Annotated[User, Depends(require_role(UserRole.REVIEWER, UserRole.ADMIN))]


@dataclass
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: Annotated[int, Query(ge=1, description="1 = the first page")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="items per page")] = 50,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


Paging = Annotated[PageParams, Depends(page_params)]


def paginate(db: Session, stmt: Select[Any], paging: PageParams) -> tuple[Sequence[Any], int]:
    """One page of `stmt`'s rows + how many rows there are in all. `stmt` must be ordered."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.limit(paging.page_size).offset(paging.offset)).all()
    return rows, total


def get_or_404[T](db: Session, model: type[T], object_id: int, name: str) -> T:
    found = db.get(model, object_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{name} {object_id} not found")
    return found
