"""Rule-based event triage.

Understanding every event is wasted work: a page view costs the same as a cancellation
request at the API, but is worth almost nothing as memory. Cheap rules run first and only
meaningful events reach extraction.
"""

from __future__ import annotations

from typing import Any

# Baseline importance per event type. Projects override these in project settings.
DEFAULT_EVENT_IMPORTANCE: dict[str, float] = {
    # Low value telemetry
    "page_view": 0.05,
    "page_viewed": 0.05,
    "mouse_move": 0.0,
    "heartbeat": 0.0,
    "health_check": 0.0,
    "session_started": 0.05,
    "session_ended": 0.05,
    "click": 0.05,
    # Ordinary product activity
    "feature_used": 0.25,
    "login": 0.1,
    "integration_connected": 0.6,
    "integration_failed": 0.8,
    "onboarding_completed": 0.5,
    "profile_updated": 0.3,
    # High value signals
    "support_message": 0.75,
    "support_ticket_created": 0.8,
    "feedback": 0.7,
    "feedback_submitted": 0.7,
    "nps_submitted": 0.7,
    "goal_created": 0.6,
    "purchase": 0.8,
    "order_placed": 0.7,
    "payment_failed": 0.85,
    "subscription_changed": 0.85,
    "subscription_upgraded": 0.8,
    "subscription_downgraded": 0.9,
    "cancellation_requested": 0.98,
    "subscription_cancelled": 0.98,
    "churned": 0.98,
}

DEFAULT_IMPORTANCE = 0.4

# Substrings that mark an event type as high-signal even when it is not in the table.
_HIGH_SIGNAL_MARKERS = (
    "cancel", "churn", "refund", "downgrade", "upgrade", "payment", "invoice",
    "support", "ticket", "complaint", "feedback", "error", "failed", "failure",
    "purchase", "order", "subscription", "goal", "intent",
)
_LOW_SIGNAL_MARKERS = ("view", "impression", "scroll", "hover", "heartbeat", "ping", "health")

# Text fields carry most of the meaning; an event with one is worth a closer look.
_TEXT_FIELDS = ("message", "text", "body", "comment", "feedback", "reason", "note", "content")


def base_importance(event_type: str, overrides: dict[str, float] | None = None) -> float:
    """Importance of an event type before looking at its payload."""
    key = event_type.strip().lower()
    if overrides and key in overrides:
        return _clamp(float(overrides[key]))
    if key in DEFAULT_EVENT_IMPORTANCE:
        return DEFAULT_EVENT_IMPORTANCE[key]
    if any(marker in key for marker in _HIGH_SIGNAL_MARKERS):
        return 0.7
    if any(marker in key for marker in _LOW_SIGNAL_MARKERS):
        return 0.05
    return DEFAULT_IMPORTANCE


def score_event(
    *,
    event_type: str,
    data: dict[str, Any],
    overrides: dict[str, float] | None = None,
) -> float:
    """Importance of a specific event, combining its type and its payload."""
    score = base_importance(event_type, overrides)

    text = " ".join(
        str(value)
        for key, value in (data or {}).items()
        if key.lower() in _TEXT_FIELDS and isinstance(value, str)
    ).strip()
    if text:
        # A human wrote something: that is almost always worth remembering.
        score = max(score, 0.6)
        if len(text) > 120:
            score = min(1.0, score + 0.05)

    if (data or {}).get("error") or (data or {}).get("failed") is True:
        score = min(1.0, score + 0.15)

    explicit = (data or {}).get("importance")
    if isinstance(explicit, (int, float)):
        score = _clamp(float(explicit))

    return round(_clamp(score), 4)


def should_extract(importance: float, threshold: float) -> bool:
    return importance >= threshold


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
