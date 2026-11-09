"""Password hashing and the API's login tokens.

Passwords: pwdlib with Argon2, the setup the FastAPI security docs recommend. The seed
command and `POST /api/users` hash with `hash_password()`; login checks with
`verify_password()`.

Tokens: a JWT signed with HS256 (PyJWT, also what the FastAPI docs use). It holds the
user's id (`sub`), an expiry (`exp`, JWT_EXPIRE_MINUTES) and the user's token version
(`ver`); there are no refresh tokens. `POST /api/auth/login` makes one; `app.api.deps`
reads it on every request and refuses it once the user's `token_version` has moved on
(a password change bumps it), so a token can be ended before it expires.

Every token says what it's for (`typ`): `access` for a login, `2fa` for the 5-minute token
between the password and the authenticator code. Each reader accepts only its own kind,
so the half-way token can't be used as a login (tokens made before `typ` existed count as
`access`).

Password reset links carry a random token (`new_reset_token()`); the database keeps only
its SHA-256 (`hash_reset_token()`), so a copy of the table can't be turned into links.

Two-factor codes are TOTP (RFC 6238, 30-second steps, pyotp), the kind every
authenticator app makes. `totp_step()` accepts the code for now or one step either side
(clocks drift) and returns that step; the caller refuses a step it has already seen, so a
code works once.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache

import jwt
import pyotp
import segno
from pwdlib import PasswordHash

ALGORITHM = "HS256"
ACCESS = "access"
TWO_FACTOR = "2fa"
# How long the half-way token (password right, code still to come) lasts.
TWO_FACTOR_MINUTES = 5
TOTP_ISSUER = "leadgate"

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
    return _encode(user_id, secret, now, timedelta(minutes=expire_minutes), version, ACCESS)


def read_access_token(token: str, secret: str) -> TokenClaims:
    """The claims of a valid login token. Raises `jwt.InvalidTokenError` if it's bad,
    expired or another kind of token."""
    return _decode(token, secret, ACCESS)


def create_two_factor_token(user_id: int, secret: str, now: datetime, version: int) -> str:
    """The half-way token: the password was right, the authenticator code comes next."""
    return _encode(user_id, secret, now, timedelta(minutes=TWO_FACTOR_MINUTES), version, TWO_FACTOR)


def read_two_factor_token(token: str, secret: str) -> TokenClaims:
    return _decode(token, secret, TWO_FACTOR)


def _encode(
    user_id: int, secret: str, now: datetime, lifetime: timedelta, version: int, kind: str
) -> str:
    claims = {"sub": str(user_id), "ver": version, "typ": kind, "iat": now, "exp": now + lifetime}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def _decode(token: str, secret: str, kind: str) -> TokenClaims:
    claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    # Login tokens made before "typ" existed have none.
    if claims.get("typ", ACCESS) != kind:
        raise jwt.InvalidTokenError(f"not a {kind} token")
    try:
        # Tokens made before versions existed have no "ver": they count as version 0.
        return TokenClaims(user_id=int(claims["sub"]), version=int(claims.get("ver", 0)))
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("sub or ver is not a number") from exc


def new_reset_token() -> str:
    """256 random bits, URL-safe: the secret part of a password reset link."""
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    """What the setup QR code holds (otpauth://...), for an authenticator app."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def qr_svg(data: str) -> str:
    """A QR code as an SVG data URI, shown with an <img> (no script, so the CSP allows it)."""
    return segno.make(data, error="m").svg_data_uri(scale=4, border=2)


def totp_step(secret: str, code: str, now: datetime) -> int | None:
    """The 30-second step `code` belongs to (now, or one step either side), or None if it
    matches none. The caller refuses a step it has already used."""
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = totp.timecode(now)
    for step in (current, current - 1, current + 1):
        if secrets.compare_digest(totp.generate_otp(step), code):
            return step
    return None
