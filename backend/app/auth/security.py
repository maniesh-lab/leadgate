"""Password hashing.

Uses pwdlib with Argon2, the setup the FastAPI security docs recommend. The seed
command hashes the admin password with it; Phase 7's login checks passwords with
`verify_password()` and adds the JWT helpers here.
"""

from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)
