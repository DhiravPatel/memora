"""Outbound webhooks: signing, payloads and dispatch.

The receiving side of the product (``packages/integrations``) and the sending side share
nothing but a philosophy: signatures are mandatory, payloads are versioned, and every
attempt is recorded.
"""

from webhooks.dispatcher import WebhookDispatcher
from webhooks.events import (
    PAYLOAD_VERSION,
    OutboundEvent,
    agent_action_denied,
    agent_approval_decided,
    agent_approval_requested,
    customer_created,
    customer_deleted,
    customer_health_changed,
    customer_state_changed,
    event_failed,
    goal_changed,
    memory_conflict,
    memory_created,
    memory_updated,
    signal_raised,
)
from webhooks.signature import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    new_secret,
    sign,
    verify,
)

__all__ = [
    "DELIVERY_HEADER",
    "EVENT_HEADER",
    "PAYLOAD_VERSION",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "OutboundEvent",
    "WebhookDispatcher",
    "agent_action_denied",
    "agent_approval_decided",
    "agent_approval_requested",
    "customer_created",
    "customer_deleted",
    "customer_health_changed",
    "customer_state_changed",
    "event_failed",
    "goal_changed",
    "memory_conflict",
    "memory_created",
    "memory_updated",
    "new_secret",
    "signal_raised",
    "sign",
    "verify",
]
