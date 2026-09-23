"""Encryption at rest for the two secrets that cannot be hashed.

Signing needs the value back, so webhook secrets and inbound provider secrets are the one
place this system stores something recoverable. These tests pin the properties that make
that safe: a database dump is not enough, rotation works, and a deployment that has not
turned encryption on yet keeps working.
"""

from __future__ import annotations

import pytest

from app.services.settings_service import validate
from common.crypto import (
    EncryptionError,
    decrypt,
    derive_key,
    encrypt,
    generate_key,
    is_encrypted,
)

KEY = derive_key("a-strong-encryption-key-for-tests-32")
OTHER = derive_key("a-different-encryption-key-for-tests")


def test_ciphertext_does_not_contain_the_secret():
    blob = encrypt("whsec_super_secret_value", KEY)
    assert "whsec_super_secret_value" not in blob
    assert is_encrypted(blob)
    assert blob.startswith("enc:v1:")


def test_round_trip():
    assert decrypt(encrypt("whsec_abc", KEY), KEY) == "whsec_abc"


def test_the_same_value_encrypts_differently_every_time():
    """A random nonce per write: equal secrets must not produce equal ciphertext."""
    first, second = encrypt("same", KEY), encrypt("same", KEY)
    assert first != second
    assert decrypt(first, KEY) == decrypt(second, KEY) == "same"


def test_the_wrong_key_cannot_read_it():
    with pytest.raises(EncryptionError):
        decrypt(encrypt("whsec_abc", KEY), OTHER)


def test_tampering_is_detected():
    """GCM authenticates: a flipped byte fails rather than decrypting to nonsense."""
    blob = encrypt("whsec_abc", KEY)
    head, payload = blob.rsplit(":", 1)
    tampered = f"{head}:{'A' if payload[0] != 'A' else 'B'}{payload[1:]}"
    with pytest.raises(EncryptionError):
        decrypt(tampered, KEY)


def test_additional_data_binds_a_secret_to_its_row():
    blob = encrypt("whsec_abc", KEY, aad="whe_1")
    assert decrypt(blob, KEY, aad="whe_1") == "whsec_abc"
    with pytest.raises(EncryptionError):
        decrypt(blob, KEY, aad="whe_2")


def test_rotation_reads_old_values_and_writes_new_ones():
    old_blob = encrypt("whsec_old", OTHER)
    # Active key first, previous keys after: the old row still opens.
    assert decrypt(old_blob, [KEY, OTHER]) == "whsec_old"
    # And anything written now uses the active key.
    assert decrypt(encrypt("whsec_new", KEY), [KEY, OTHER]) == "whsec_new"


def test_plaintext_written_before_encryption_was_enabled_still_reads():
    """The rollout property: old rows keep working until they are rewritten."""
    assert decrypt("whsec_legacy_plaintext", KEY) == "whsec_legacy_plaintext"
    assert is_encrypted("whsec_legacy_plaintext") is False


def test_encrypting_an_encrypted_value_is_a_no_op():
    blob = encrypt("whsec_abc", KEY)
    assert encrypt(blob, KEY) == blob


def test_empty_values_pass_through():
    assert encrypt("", KEY) == ""
    assert decrypt("", KEY) == ""


def test_a_generated_key_is_usable():
    key = derive_key(generate_key())
    assert decrypt(encrypt("whsec_abc", key), key) == "whsec_abc"


def test_derive_key_rejects_nothing():
    with pytest.raises(EncryptionError):
        derive_key("")


def test_the_same_configured_secret_always_derives_the_same_key():
    """Two API replicas must agree, without coordinating."""
    assert derive_key("shared-secret").id == derive_key("shared-secret").id
    assert derive_key("shared-secret").material == derive_key("shared-secret").material


def test_provider_signing_secrets_are_encrypted_by_settings_validation(monkeypatch):
    """No write path can store one of these in plaintext."""
    from common.settings import get_settings

    monkeypatch.setattr(
        type(get_settings()), "encryption_keys", property(lambda self: [KEY])
    )

    cleaned = validate(
        {"integrations": {"stripe": {"signing_secret": "whsec_live_value", "allow_unsigned": False}}}
    )
    stored = cleaned["integrations"]["stripe"]["signing_secret"]
    assert is_encrypted(stored)
    assert "whsec_live_value" not in stored
    assert decrypt(stored, KEY) == "whsec_live_value"
    # Everything else about the provider config is untouched.
    assert cleaned["integrations"]["stripe"]["allow_unsigned"] is False


def test_settings_validation_does_not_double_encrypt(monkeypatch):
    from common.settings import get_settings

    monkeypatch.setattr(
        type(get_settings()), "encryption_keys", property(lambda self: [KEY])
    )
    once = validate({"integrations": {"stripe": {"signing_secret": "whsec_live"}}})
    twice = validate(once)
    assert twice["integrations"]["stripe"]["signing_secret"] == (
        once["integrations"]["stripe"]["signing_secret"]
    )


def test_settings_validation_is_a_no_op_without_a_key(monkeypatch):
    from common.settings import get_settings

    monkeypatch.setattr(type(get_settings()), "encryption_keys", property(lambda self: []))
    cleaned = validate({"integrations": {"stripe": {"signing_secret": "whsec_live"}}})
    assert cleaned["integrations"]["stripe"]["signing_secret"] == "whsec_live"
