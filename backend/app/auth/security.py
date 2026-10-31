"""Password hashing and the API's login tokens.

Passwords: pwdlib with Argon2, the setup the FastAPI security docs recommend. The seed
command and `POST /api/users` hash with `hash_password()`; login checks with
`verify_password()`.

Tokens: a JWT signed with HS256 (PyJWT, also what the FastAPI docs use). It holds the
user's id (`sub`), an expiry (`exp`, JWT_EXPIRE_MINUTES) and the user's token version
(`ver`); there are no refresh tokens. `POST /api/auth/login` makes one; `app.api.deps`
reads it on every request and refuses it once the user's `token_version` has moved on
(a password change bumps it), so a token can be ended before it expires.
"""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    # The user's `token_version` when the token was made.
    version: int


def create_access_token(
    user_id: int, secret: str, expire_minutes: int, now: datetime, version: int = 0
) -> str:
    claims = {
        "sub": str(user_id),
        "ver": version,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_access_token(token: str, secret: str) -> TokenClaims:
    """The claims of a valid token. Raises `jwt.InvalidTokenError` if it's bad or expired."""
    claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    try:
        # Tokens made before versions existed have no "ver": they count as version 0.
        return TokenClaims(user_id=int(claims["sub"]), version=int(claims.get("ver", 0)))
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("sub or ver is not a number") from exc
