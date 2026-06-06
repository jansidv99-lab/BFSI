import os
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-do-not-use-in-prod")

from auth.passwords import hash_password, verify_password
from auth.tokens import (
    _ALGORITHM,
    _get_secret,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_token_expired,
)

# ── password hashing ──────────────────────────────────────────────────────────

def test_hash_is_not_plain():
    assert hash_password("secret") != "secret"


def test_two_hashes_differ():
    assert hash_password("secret") != hash_password("secret")


def test_verify_correct_password():
    assert verify_password("secret", hash_password("secret")) is True


def test_verify_wrong_password():
    assert verify_password("wrong", hash_password("secret")) is False


def test_verify_empty_password():
    assert verify_password("", hash_password("abc")) is False


def test_hash_returns_string():
    assert isinstance(hash_password("x"), str)


def test_hash_is_bcrypt_format():
    assert hash_password("x").startswith("$2b$")


# ── token creation ────────────────────────────────────────────────────────────

def test_access_token_is_string():
    token = create_access_token(1, "alice")
    assert isinstance(token, str) and len(token) > 0


def test_access_token_type():
    payload = decode_token(create_access_token(1, "alice"))
    assert payload["type"] == "access"


def test_access_token_sub():
    payload = decode_token(create_access_token(1, "alice"))
    assert payload["sub"] == "1"


def test_access_token_username():
    payload = decode_token(create_access_token(1, "alice"))
    assert payload["username"] == "alice"


def test_refresh_token_is_string():
    token = create_refresh_token(1)
    assert isinstance(token, str) and len(token) > 0


def test_refresh_token_type():
    payload = decode_token(create_refresh_token(1))
    assert payload["type"] == "refresh"


def test_refresh_token_no_username():
    payload = decode_token(create_refresh_token(1))
    assert "username" not in payload


def test_access_and_refresh_differ():
    assert create_access_token(1, "alice") != create_refresh_token(1)


# ── token validation ──────────────────────────────────────────────────────────

def test_decode_valid_access_token():
    payload = decode_token(create_access_token(99, "bob"))
    assert int(payload["sub"]) == 99


def test_decode_tampered_token_raises():
    token = create_access_token(1, "alice")
    tampered = token[:-3] + "xxx"
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(tampered)


def test_decode_wrong_secret_raises():
    payload = {
        "sub": "1",
        "type": "access",
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
    }
    bad_token = pyjwt.encode(payload, "wrong-secret", algorithm=_ALGORITHM)
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(bad_token)


def test_is_token_expired_false_for_fresh():
    assert is_token_expired(create_access_token(1, "alice")) is False


def test_is_token_expired_true_for_expired():
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": "1",
        "type": "access",
        "iat": now - timedelta(minutes=60),
        "exp": now - timedelta(seconds=1),
    }
    expired_token = pyjwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)
    assert is_token_expired(expired_token) is True


# ── missing secret key ────────────────────────────────────────────────────────

def test_missing_secret_raises(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        create_access_token(1, "alice")
