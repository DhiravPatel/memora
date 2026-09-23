"""Event normalization: flatten, redact and score an event before extraction."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common.pii import redact_payload, redact_text
from common.text import normalize
from memory_engine.extraction.filters import score_event
from memory_engine.schemas import NormalizedEvent

MAX_TEXT_LENGTH = 4000
_SKIP_KEYS = {"importance", "metadata", "_meta"}


def flatten_payload(data: dict[str, Any], *, prefix: str = "") -> list[str]:
    """Render a nested payload as ``key: value`` lines for entity scanning and logging."""
    lines: list[str] = []
    for key, value in (data or {}).items():
        if key in _SKIP_KEYS:
            continue
        label = f"{prefix}{key}"
        if isinstance(value, dict):
            lines.extend(flatten_payload(value, prefix=f"{label}."))
        elif isinstance(value, list):
            rendered = ", ".join(
                str(item) for item in value if isinstance(item, (str, int, float, bool))
            )
            if rendered:
                lines.append(f"{label}: {rendered}")
        elif isinstance(value, (str, int, float, bool)):
            lines.append(f"{label}: {value}")
    return lines


def normalize_event(
    *,
    event_id: str,
    project_id: str,
    customer_id: str,
    event_type: str,
    data: dict[str, Any],
    occurred_at: datetime,
    importance_overrides: dict[str, float] | None = None,
    redact_pii: bool = True,
) -> NormalizedEvent:
    payload: dict[str, Any] = redact_payload(data or {}) if redact_pii else dict(data or {})
    text = normalize(" \n".join(flatten_payload(payload)))[:MAX_TEXT_LENGTH]
    if redact_pii:
        text = redact_text(text)

    importance = score_event(
        event_type=event_type, data=data or {}, overrides=importance_overrides
    )
    return NormalizedEvent(
        event_id=event_id,
        project_id=project_id,
        customer_id=customer_id,
        event_type=event_type.strip().lower(),
        data=payload,
        text=text,
        occurred_at=occurred_at,
        importance=importance,
    )
