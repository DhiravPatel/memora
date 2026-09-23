"""Column types that carry behaviour.

``EncryptedSecret`` is the only one: a string column whose value is encrypted on the way
into the database and decrypted on the way out. Doing it in the column type rather than at
the call sites means no future code path can forget — a plain ``endpoint.secret = value``
is encrypted, and reading it back gives the plaintext the signer needs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, TypeDecorator

from common.crypto import EncryptionError, decrypt, encrypt
from common.logging import get_logger

logger = get_logger(__name__)


class EncryptedSecret(TypeDecorator):
    """AES-GCM at rest, plaintext in Python.

    Encryption is skipped when no key is configured, so an existing deployment can adopt
    this without a flag day: rows written before the key existed keep working (``decrypt``
    returns unrecognised values unchanged), and each one becomes ciphertext the next time
    it is written.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 512) -> None:
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        keys = _keys()
        if not keys:
            return value
        return encrypt(str(value), keys[0])

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        keys = _keys()
        if not keys:
            # A key was configured when this row was written and is not configured now.
            # Returning ciphertext would silently sign deliveries with the wrong secret,
            # so fail loudly instead.
            from common.crypto import is_encrypted

            if is_encrypted(value):
                raise EncryptionError(
                    "This value is encrypted but SECRETS_ENCRYPTION_KEY is not set."
                )
            return value
        return decrypt(str(value), keys)


def _keys() -> list[Any]:
    from common.settings import get_settings

    return get_settings().encryption_keys
