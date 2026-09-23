"""Signing outbound webhooks.

Same scheme the ecosystem already expects (Stripe-style): a timestamped HMAC over
``<timestamp>.<body>``, so a receiver can reject both forgeries and replays. The helper
that verifies it ships in both SDKs, because a webhook a customer cannot verify is a
webhook they should not trust.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Memora-Signature"
TIMESTAMP_HEADER = "X-Memora-Timestamp"
EVENT_HEADER = "X-Memora-Event"
DELIVERY_HEADER = "X-Memora-Delivery"
DEFAULT_TOLERANCE_SECONDS = 300


def sign(*, secret: str, body: bytes, timestamp: int | None = None) -> tuple[str, int]:
    """Return ``(signature_header_value, timestamp)``."""
    issued_at = timestamp or int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"), f"{issued_at}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={issued_at},v1={digest}", issued_at


def verify(
    *,
    secret: str,
    body: bytes,
    header: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """Verify a signature header produced by :func:`sign`."""
    if not secret or not header:
        return False

    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    timestamp = parts.get("t")
    provided = parts.get("v1")
    if not timestamp or not provided:
        return False

    try:
        age = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    if age > tolerance_seconds:
        return False

    expected, _ = sign(secret=secret, body=body, timestamp=int(float(timestamp)))
    expected_digest = expected.split("v1=", 1)[1]
    return hmac.compare_digest(expected_digest, provided.strip())


def new_secret() -> str:
    import secrets

    return f"whsec_{secrets.token_urlsafe(32)}"
