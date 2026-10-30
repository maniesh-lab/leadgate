"""Password hashing and the API's login tokens.

Passwords: pwdlib with Argon2, the setup the FastAPI security docs recommend. The seed
command and `POST /api/users` hash with `hash_password()`; login checks with
`verify_password()`.

Tokens: a JWT signed with HS256 (PyJWT, also what the FastAPI docs use). It holds the
user's id (`sub`) and an expiry (`exp`, JWT_EXPIRE_MINUTES); there are no refresh
tokens. `POST /api/auth/login` makes one; `app.api.deps` reads it on every request.
"""

from datetime import datetime, timedelta
from functools import cache

import jwt
from pwdlib import PasswordHash

ALGORITHM = "HS256"

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)


@cache
def dummy_hash() -> str:
    """A hash to check against when the email is unknown.

    Login then takes about as long for an unknown email as for a wrong password, so the
    response time doesn't tell an attacker which emails exist. Made once, on first use.
    """
    return hash_password("not a real password")


def create_access_token(user_id: int, secret: str, expire_minutes: int, now: datetime) -> str:
    claims = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=expire_minutes)}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_access_token(token: str, secret: str) -> int:
    """The user id in a valid token. Raises `jwt.InvalidTokenError` if it's bad or expired."""
    claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    try:
        return int(claims["sub"])
    except ValueError as exc:
        raise jwt.InvalidTokenError("sub is not a user id") from exc
