"""Tests for password hashing and login tokens."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.security import (
    ALGORITHM,
    create_access_token,
    dummy_hash,
    hash_password,
    read_access_token,
    verify_password,
)


def test_hash_is_argon2_and_verifies() -> None:
    hashed = hash_password("correct horse")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong horse", hashed)


def test_same_password_hashes_differently_each_time() -> None:
    # A random salt per hash means two users with one password don't share a hash.
    assert hash_password("same") != hash_password("same")


SECRET = "s" * 32


def test_token_round_trip() -> None:
    token = create_access_token(7, SECRET, 60, datetime.now(UTC))
    assert read_access_token(token, SECRET) == 7


def test_expired_token_is_refused() -> None:
    token = create_access_token(7, SECRET, 60, datetime.now(UTC) - timedelta(minutes=61))
    with pytest.raises(jwt.ExpiredSignatureError):
        read_access_token(token, SECRET)


def test_token_signed_with_another_secret_is_refused() -> None:
    token = create_access_token(7, "x" * 32, 60, datetime.now(UTC))
    with pytest.raises(jwt.InvalidSignatureError):
        read_access_token(token, SECRET)


def test_token_without_a_user_id_is_refused() -> None:
    claims = {"sub": "admin", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    with pytest.raises(jwt.InvalidTokenError):
        read_access_token(jwt.encode(claims, SECRET, algorithm=ALGORITHM), SECRET)
    with pytest.raises(jwt.MissingRequiredClaimError):
        read_access_token(jwt.encode({"sub": "7"}, SECRET, algorithm=ALGORITHM), SECRET)


def test_dummy_hash_is_made_once() -> None:
    assert dummy_hash() is dummy_hash()
    assert not verify_password("anything", dummy_hash())
