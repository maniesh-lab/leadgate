"""Tests for password hashing."""

from app.auth.security import hash_password, verify_password


def test_hash_is_argon2_and_verifies() -> None:
    hashed = hash_password("correct horse")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong horse", hashed)


def test_same_password_hashes_differently_each_time() -> None:
    # A random salt per hash means two users with one password don't share a hash.
    assert hash_password("same") != hash_password("same")
