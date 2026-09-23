"""Prefixed, URL-safe, sortable identifiers.

Public identifiers are prefixed (``mem_``, ``evt_`` …) so that a value is always
traceable back to the resource it belongs to, which matters for audit logs and for
debugging AI answers that cite evidence.
"""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(number: int) -> str:
    if number == 0:
        return "0"
    out: list[str] = []
    while number:
        number, remainder = divmod(number, 36)
        out.append(_ALPHABET[remainder])
    return "".join(reversed(out))


def new_id(prefix: str, random_length: int = 12) -> str:
    """Return a time-ordered identifier such as ``mem_lz4f8k2q9x1abc``."""
    timestamp = _base36(int(time.time() * 1000))
    random_part = "".join(secrets.choice(_ALPHABET) for _ in range(random_length))
    return f"{prefix}_{timestamp}{random_part}"


def new_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)
