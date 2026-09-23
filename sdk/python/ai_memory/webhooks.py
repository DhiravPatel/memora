"""Verifying inbound webhooks from the Memory Layer.

Stdlib only — a receiver should not need this SDK's HTTP dependency just to check a
signature, so this module imports nothing outside Python itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SIGNATURE_HEADER = "X-Memora-Signature"
TIMESTAMP_HEADER = "X-Memora-Timestamp"
EVENT_HEADER = "X-Memora-Event"
DELIVERY_HEADER = "X-Memora-Delivery"
DEFAULT_TOLERANCE_SECONDS = 300


class WebhookVerificationError(Exception):
    """Raised when a payload does not verify against the signing secret."""


@dataclass(slots=True)
class WebhookEvent:
    id: str
    type: str
    version: str
    created_at: str
    project_id: str
    data: dict[str, Any]

    @property
    def is_health_alert(self) -> bool:
        return self.type in ("customer.at_risk", "customer.recovered", "customer.health_changed")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> WebhookEvent:
        return cls(
            id=str(payload.get("id", "")),
            type=str(payload.get("type", "")),
            version=str(payload.get("version", "")),
            created_at=str(payload.get("created_at", "")),
            project_id=str(payload.get("project_id", "")),
            data=dict(payload.get("data") or {}),
        )


def verify_signature(
    *,
    secret: str,
    payload: bytes | str,
    signature_header: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """Verify the ``X-Memora-Signature`` header against the raw request body."""
    if not secret or not signature_header:
        return False

    body = payload.encode("utf-8") if isinstance(payload, str) else payload
    parts = dict(piece.split("=", 1) for piece in signature_header.split(",") if "=" in piece)
    timestamp = parts.get("t")
    provided = parts.get("v1")
    if not timestamp or not provided:
        return False

    try:
        age = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    if age > tolerance_seconds:
        return False  # replayed, or the sender's clock is badly wrong

    expected = hmac.new(
        secret.encode("utf-8"), f"{int(float(timestamp))}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided.strip())


def construct_event(
    *,
    secret: str,
    payload: bytes | str,
    signature_header: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> WebhookEvent:
    """Verify and parse in one step.

    ```python
    event = construct_event(
        secret=os.environ["MEMORY_WEBHOOK_SECRET"],
        payload=request.data,                       # the raw body, not the parsed JSON
        signature_header=request.headers["X-Memora-Signature"],
    )
    if event.type == "customer.at_risk":
        escalate(event.data["customer"], event.data["explanation"])
    ```
    """
    if not verify_signature(
        secret=secret,
        payload=payload,
        signature_header=signature_header,
        tolerance_seconds=tolerance_seconds,
    ):
        raise WebhookVerificationError(
            "Webhook signature did not verify. Check the secret, and make sure you are "
            "passing the raw request body rather than a re-serialised dict."
        )

    body = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    return WebhookEvent.from_payload(json.loads(body))
