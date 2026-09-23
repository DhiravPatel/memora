"""Integration contracts.

An integration does exactly two things: prove a payload came from the provider, and
translate it into the internal event schema. It never talks to the database and never
decides what a memory is — that keeps every connector small, testable and replaceable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from common.time import ensure_utc, utcnow


@dataclass(slots=True)
class NormalizedEvent:
    """An external payload expressed in the internal event schema."""

    customer_id: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    external_event_id: str | None = None
    occurred_at: datetime | None = None
    customer_email: str | None = None
    customer_name: str | None = None
    source: str = "integration"

    def as_payload(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "event_type": self.event_type,
            "data": self.data,
            "external_event_id": self.external_event_id,
            "occurred_at": (ensure_utc(self.occurred_at) if self.occurred_at else utcnow()),
            "customer_email": self.customer_email,
            "customer_name": self.customer_name,
            "source": self.source,
        }


class Integration(Protocol):
    """Provider connector."""

    name: str
    # Header carrying the provider's signature, if it signs at all.
    signature_header: str | None

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        """Is this payload authentic?"""

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        """Translate a webhook body into zero or more internal events."""


def first_present(payload: dict[str, Any], *paths: str, default: Any = None) -> Any:
    """Read the first present dotted path from a nested payload."""
    for path in paths:
        current: Any = payload
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current not in (None, "", [], {}):
            return current
    return default


def timestamp_from(value: Any) -> datetime | None:
    """Accept unix seconds, unix millis or ISO-8601."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=__import__("datetime").timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return ensure_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None
