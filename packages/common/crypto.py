"""Encryption at rest for the few secrets that cannot be hashed.

Most credentials here are one-way: passwords are bcrypt hashes, API keys are SHA-256
hashes, and nothing ever needs the original. Two things cannot work that way, because
signing and verification both need the *value*:

* the outbound webhook secret, used to HMAC every delivery;
* an inbound integration's signing secret, used to verify what a provider sends us.

Those are encrypted with AES-256-GCM under a key that lives outside the database — in
production, a KMS-held key injected as ``SECRETS_ENCRYPTION_KEY``. A database dump on its
own is then not enough to forge a delivery or replay a provider's webhook.

Ciphertext is stored as a self-describing string::

    enc:v1:<key_id>:<base64url(nonce || ciphertext || tag)>

The key id is carried so keys can be rotated: a new key gets a new id, old rows keep
decrypting under the previous one until they are rewritten.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc"
VERSION = "v1"
NONCE_BYTES = 12
KEY_BYTES = 32


class EncryptionError(RuntimeError):
    """Raised when a value cannot be encrypted or decrypted."""


@dataclass(slots=True, frozen=True)
class EncryptionKey:
    """One key and the id stored alongside anything it encrypted."""

    id: str
    material: bytes

    @property
    def aesgcm(self) -> AESGCM:
        return AESGCM(self.material)


def derive_key(secret: str, *, key_id: str | None = None) -> EncryptionKey:
    """Turn a configured secret into a 32-byte key.

    Accepts either raw base64 key material (what a KMS hands you) or an arbitrary
    passphrase, which is stretched with SHA-256 so a short value in a ``.env`` file still
    produces a full-width key. The id is derived from the material, so two deployments
    configured with the same key agree on it without coordinating.
    """
    if not secret:
        raise EncryptionError("No encryption key configured.")

    material: bytes | None = None
    try:
        candidate = base64.urlsafe_b64decode(_pad(secret))
        if len(candidate) == KEY_BYTES:
            material = candidate
    except (ValueError, TypeError):
        material = None

    if material is None:
        material = hashlib.sha256(secret.encode("utf-8")).digest()

    return EncryptionKey(
        id=key_id or hashlib.sha256(material).hexdigest()[:8],
        material=material,
    )


def generate_key() -> str:
    """A fresh base64 key, for `openssl`-free key generation in a setup guide."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii").rstrip("=")


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(f"{PREFIX}:{VERSION}:")


def encrypt(value: str, key: EncryptionKey, *, aad: str | None = None) -> str:
    """Encrypt a value. ``aad`` binds the ciphertext to its context (e.g. the row id)."""
    if not value:
        return value
    if is_encrypted(value):
        return value

    nonce = os.urandom(NONCE_BYTES)
    sealed = key.aesgcm.encrypt(
        nonce, value.encode("utf-8"), aad.encode("utf-8") if aad else None
    )
    blob = base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")
    return f"{PREFIX}:{VERSION}:{key.id}:{blob}"


def key_id_of(value: str | None) -> str | None:
    """Which key sealed this value, or ``None`` if it is not sealed at all.

    Reads only the header, so it answers for a value this process holds no key for — which
    is exactly the case a rotation check has to report on.
    """
    if not value or not is_encrypted(value):
        return None
    try:
        _, _, key_id, _ = value.split(":", 3)
    except ValueError:
        return None
    return key_id


def needs_rewrite(value: str | None, active_id: str) -> bool:
    """True when a value is plaintext, or sealed under a key that is no longer active."""
    if not value:
        return False
    if not is_encrypted(value):
        return True
    return key_id_of(value) != active_id


def decrypt(value: str, keys: EncryptionKey | list[EncryptionKey], *, aad: str | None = None) -> str:
    """Decrypt a value, tolerating plaintext written before encryption was turned on.

    Returning legacy plaintext unchanged is what makes the rollout safe: the migration can
    encrypt rows in the background while both old and new rows keep working.
    """
    if not value or not is_encrypted(value):
        return value

    candidates = [keys] if isinstance(keys, EncryptionKey) else list(keys)
    if not candidates:
        raise EncryptionError("No encryption key available to decrypt this value.")

    try:
        _, _, key_id, blob = value.split(":", 3)
        raw = base64.urlsafe_b64decode(_pad(blob))
        nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    except (ValueError, TypeError) as exc:
        raise EncryptionError("Stored value is not valid ciphertext.") from exc

    # Try the key it was written with first, then any others (a rotation in progress).
    ordered = [key for key in candidates if key.id == key_id] + [
        key for key in candidates if key.id != key_id
    ]
    for key in ordered:
        try:
            return key.aesgcm.decrypt(
                nonce, sealed, aad.encode("utf-8") if aad else None
            ).decode("utf-8")
        except InvalidTag:
            continue

    raise EncryptionError(
        f"Could not decrypt a value written under key '{key_id}'. "
        "Is SECRETS_ENCRYPTION_KEY the same key the value was written with?"
    )


def _pad(value: str) -> str:
    """base64url without padding is what we store; add it back before decoding."""
    return value + "=" * (-len(value) % 4)
