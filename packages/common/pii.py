"""PII detection and redaction.

Raw events routinely carry emails, phone numbers and card-like strings. Those values
must never reach an AI provider or a memory body, so text is scrubbed on the way into
the memory engine while the immutable event keeps whatever the customer sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

EMAIL_RE = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w)")
CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SECRET_RE = re.compile(
    r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b|\bBearer\s+[A-Za-z0-9._-]{16,}\b"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("secret", SECRET_RE, "[REDACTED_SECRET]"),
    ("email", EMAIL_RE, "[REDACTED_EMAIL]"),
    ("ssn", SSN_RE, "[REDACTED_SSN]"),
    ("card", CARD_RE, "[REDACTED_CARD]"),
    ("phone", PHONE_RE, "[REDACTED_PHONE]"),
    ("ip", IP_RE, "[REDACTED_IP]"),
)

# Keys whose values are redacted wholesale regardless of their content.
SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "card_number",
    "cvv",
    "ssn",
}


@dataclass(frozen=True)
class PIIFinding:
    kind: str
    count: int


def detect(text: str) -> list[PIIFinding]:
    findings: list[PIIFinding] = []
    for kind, pattern, _ in _PATTERNS:
        matches = pattern.findall(text)
        if matches:
            findings.append(PIIFinding(kind=kind, count=len(matches)))
    return findings


def redact_text(text: str) -> str:
    for _, pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_payload(payload: Any) -> Any:
    """Recursively redact a JSON-like structure."""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact_payload(value)
        return cleaned
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, str):
        return redact_text(payload)
    return payload


def contains_pii(text: str) -> bool:
    return bool(detect(text))
