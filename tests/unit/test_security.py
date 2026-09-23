"""Password hashing, JWTs and API key handling."""

from __future__ import annotations

import pytest

from app.core.security import (
    api_key_prefix,
    create_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)
from common.errors import AuthenticationError


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)


def test_short_passwords_are_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


def test_access_and_refresh_tokens_are_distinct_types():
    access = create_token("usr_1", token_type="access")
    refresh = create_token("usr_1", token_type="refresh")
    assert decode_token(access)["sub"] == "usr_1"
    assert decode_token(refresh, expected_type="refresh")["sub"] == "usr_1"
    with pytest.raises(AuthenticationError):
        decode_token(refresh, expected_type="access")


def test_tampered_token_is_rejected():
    token = create_token("usr_1")
    with pytest.raises(AuthenticationError):
        decode_token(token[:-3] + "aaa")


def test_api_keys_are_hashed_not_stored():
    key = generate_api_key("test")
    digest = hash_api_key(key)
    assert key.startswith("mk_test_")
    assert key not in digest
    assert len(digest) == 64
    assert hash_api_key(key) == digest  # deterministic lookup
    assert hash_api_key(generate_api_key("test")) != digest


def test_api_key_prefix_is_not_enough_to_authenticate():
    key = generate_api_key("live")
    prefix = api_key_prefix(key)
    assert len(prefix) < len(key)
    assert hash_api_key(prefix) != hash_api_key(key)
