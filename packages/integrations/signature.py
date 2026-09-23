"""Webhook signature verification.

Every comparison is constant-time, every scheme requires a shared secret, and an
unrecognised or missing signature is a rejection — a webhook endpoint that accepts
unsigned payloads is an open door into a customer's memory.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

DEFAULT_TOLERANCE_SECONDS = 300


def hmac_sha256_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def hmac_sha256_base64(secret: str, payload: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def hmac_sha1_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha1).hexdigest()


def compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.strip(), right.strip())


_SCHEME_PREFIX = re.compile(r"^(?:sha1|sha256|sha512|v0|v1)=", re.IGNORECASE)


def strip_scheme(signature: str) -> str:
    """Drop a ``sha256=`` style prefix without touching base64 padding."""
    return _SCHEME_PREFIX.sub("", signature.strip(), count=1)


def verify_plain(*, secret: str, raw_body: bytes, provided: str, encoding: str = "hex") -> bool:
    """Signature over the raw body only (Intercom, Zendesk, HubSpot v1, generic)."""
    if not secret or not provided:
        return False
    provided = strip_scheme(provided)
    expected = (
        hmac_sha256_hex(secret, raw_body)
        if encoding == "hex"
        else hmac_sha256_base64(secret, raw_body)
    )
    if compare(expected, provided):
        return True
    # Some providers still sign with SHA-1.
    return encoding == "hex" and compare(hmac_sha1_hex(secret, raw_body), provided)


def verify_timestamped(
    *,
    secret: str,
    raw_body: bytes,
    header: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    separator: str = ".",
) -> bool:
    """Stripe-style ``t=<timestamp>,v1=<signature>`` with replay protection."""
    if not secret or not header:
        return False

    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    timestamp = parts.get("t")
    signature = parts.get("v1") or parts.get("v0")
    if not timestamp or not signature:
        return False

    try:
        age = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    if age > tolerance_seconds:
        return False  # replayed or clock-skewed beyond tolerance

    signed_payload = f"{timestamp}{separator}".encode() + raw_body
    return compare(hmac_sha256_hex(secret, signed_payload), signature)
