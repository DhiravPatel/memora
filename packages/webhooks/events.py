"""Outbound event payloads.

Every payload has the same envelope, so a receiver can route on ``type`` and trust that
``data`` is the resource the event is about. Payloads carry ids rather than whole objects
where the object is large — the receiver can fetch what it needs with its own key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.enums import WebhookEvent
from common.ids import new_id
from common.time import utcnow

PAYLOAD_VERSION = "2026-09-18"


@dataclass(slots=True)
class OutboundEvent:
    type: WebhookEvent
    project_id: str
    data: dict[str, Any]
    id: str = field(default_factory=lambda: new_id("evn"))
    created_at: datetime = field(default_factory=utcnow)

    def envelope(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "version": PAYLOAD_VERSION,
            "created_at": self.created_at.isoformat(),
            "project_id": self.project_id,
            "data": self.data,
        }


def memory_created(*, project_id: str, memory: Any, customer: Any) -> OutboundEvent:
    return OutboundEvent(
        type=WebhookEvent.MEMORY_CREATED,
        project_id=project_id,
        data={
            "memory": _memory_payload(memory),
            "customer": _customer_ref(customer),
        },
    )


def memory_updated(*, project_id: str, memory: Any, customer: Any, reason: str = "") -> OutboundEvent:
    return OutboundEvent(
        type=WebhookEvent.MEMORY_UPDATED,
        project_id=project_id,
        data={
            "memory": _memory_payload(memory),
            "customer": _customer_ref(customer),
            "reason": reason,
        },
    )


def customer_health_changed(
    *,
    project_id: str,
    customer: Any,
    previous_band: str | None,
    score: float,
    band: str,
    explanation: str,
    factors: list[dict[str, Any]],
) -> OutboundEvent:
    event_type = WebhookEvent.CUSTOMER_HEALTH_CHANGED
    if band in ("at_risk", "critical") and previous_band not in ("at_risk", "critical"):
        event_type = WebhookEvent.CUSTOMER_AT_RISK
    elif band in ("healthy", "watch") and previous_band in ("at_risk", "critical"):
        event_type = WebhookEvent.CUSTOMER_RECOVERED

    return OutboundEvent(
        type=event_type,
        project_id=project_id,
        data={
            "customer": _customer_ref(customer),
            "previous_band": previous_band,
            "band": band,
            "score": round(float(score), 1),
            "explanation": explanation,
            "factors": factors[:6],
        },
    )


def customer_created(*, project_id: str, customer: Any) -> OutboundEvent:
    return OutboundEvent(
        type=WebhookEvent.CUSTOMER_CREATED,
        project_id=project_id,
        data={"customer": _customer_ref(customer)},
    )


def customer_deleted(*, project_id: str, customer_id: str, removed: dict[str, int]) -> OutboundEvent:
    return OutboundEvent(
        type=WebhookEvent.CUSTOMER_DELETED,
        project_id=project_id,
        data={"customer_id": customer_id, "removed": removed},
    )


def event_failed(*, project_id: str, event: Any, error: str) -> OutboundEvent:
    return OutboundEvent(
        type=WebhookEvent.EVENT_FAILED,
        project_id=project_id,
        data={
            "event": {
                "id": getattr(event, "id", None),
                "event_type": getattr(event, "event_type", None),
                "customer_id": getattr(event, "customer_id", None),
                "attempts": getattr(event, "attempts", 0),
            },
            "error": error[:500],
        },
    )


def memory_conflict(
    *, project_id: str, memory: Any, superseded_memory_id: str, reason: str
) -> OutboundEvent:
    return OutboundEvent(
        type=WebhookEvent.MEMORY_CONFLICT,
        project_id=project_id,
        data={
            "memory": _memory_payload(memory),
            "superseded_memory_id": superseded_memory_id,
            "reason": reason,
        },
    )


def goal_changed(*, project_id: str, customer: Any, change: Any) -> OutboundEvent:
    """A tracked goal reached its end state.

    Only closures are emitted. "Progressing" fires on almost every event for an active
    customer, which would make the stream noise rather than signal.
    """
    status = str(getattr(change, "status", ""))
    event_type = (
        WebhookEvent.GOAL_ACHIEVED if status == "achieved" else WebhookEvent.GOAL_ABANDONED
    )
    return OutboundEvent(
        type=event_type,
        project_id=project_id,
        data={
            "customer": _customer_ref(customer),
            "goal": change.as_dict() if hasattr(change, "as_dict") else dict(change),
        },
    )


def signal_raised(*, project_id: str, customer: Any, report: Any, signal: Any) -> OutboundEvent:
    """A strong new risk or opportunity appeared in a customer's forecast."""
    return OutboundEvent(
        type=WebhookEvent.SIGNAL_RAISED,
        project_id=project_id,
        data={
            "customer": _customer_ref(customer),
            "signal": signal.as_dict() if hasattr(signal, "as_dict") else dict(signal),
            "trajectory": str(getattr(report, "trajectory", "")),
            "churn_risk": round(float(getattr(report, "churn_risk", 0.0)), 3),
            "expansion_score": round(float(getattr(report, "expansion_score", 0.0)), 3),
            "headline": getattr(report, "headline", ""),
        },
    )


def _memory_payload(memory: Any) -> dict[str, Any]:
    return {
        "id": getattr(memory, "id", None),
        "type": str(getattr(memory, "type", "")),
        "content": getattr(memory, "content", ""),
        "importance": round(float(getattr(memory, "importance", 0)), 3),
        "confidence": round(float(getattr(memory, "confidence", 0)), 3),
        "status": str(getattr(memory, "status", "")),
        "evidence_count": getattr(memory, "evidence_count", 1),
        "source_event_ids": list(getattr(memory, "source_event_ids", []) or []),
    }


def _customer_ref(customer: Any) -> dict[str, Any]:
    if customer is None:
        return {}
    return {
        "id": getattr(customer, "id", None),
        "external_id": getattr(customer, "external_id", None),
        "name": getattr(customer, "name", None),
        "email": getattr(customer, "email", None),
    }
