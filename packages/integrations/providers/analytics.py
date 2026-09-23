"""Product analytics and CRM → internal events (PostHog, HubSpot, generic)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from integrations.base import NormalizedEvent, first_present, timestamp_from
from integrations.signature import verify_plain

# Analytics tools fire enormous volumes of low-value events; these never become memories
# and are dropped at the connector rather than costing a database write.
POSTHOG_IGNORED = {
    "$pageview", "$pageleave", "$autocapture", "$identify", "$groupidentify", "$feature_flag_called",
    "$web_vitals", "$rageclick", "$exception",
}

MAX_PROPERTIES = 25


def _clean_properties(properties: Any) -> dict[str, Any]:
    if not isinstance(properties, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in properties.items():
        if key.startswith("$") or isinstance(value, (dict, list)):
            continue
        cleaned[key] = value
        if len(cleaned) >= MAX_PROPERTIES:
            break
    return cleaned


class PostHogIntegration:
    name = "posthog"
    signature_header = "X-Posthog-Signature"

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        return verify_plain(
            secret=secret,
            raw_body=raw_body,
            provided=headers.get(self.signature_header.lower(), ""),
        )

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        items = payload.get("batch") if isinstance(payload.get("batch"), list) else [payload]
        events: list[NormalizedEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(first_present(item, "event", "name", default=""))
            if not name or name in POSTHOG_IGNORED:
                continue
            customer_id = first_present(item, "distinct_id", "properties.distinct_id", "person_id")
            if not customer_id:
                continue
            properties = _clean_properties(item.get("properties"))
            events.append(
                NormalizedEvent(
                    customer_id=str(customer_id),
                    event_type=name.strip().lower().replace(" ", "_").lstrip("$"),
                    data={"posthog_event": name, **properties},
                    external_event_id=str(first_present(item, "uuid", "id", "message_id") or ""),
                    occurred_at=timestamp_from(first_present(item, "timestamp", "sent_at")),
                    customer_email=first_present(item, "properties.email", "properties.$email"),
                    source="posthog",
                )
            )
        return events


class HubSpotIntegration:
    name = "hubspot"
    signature_header = "X-HubSpot-Signature"

    SUBSCRIPTION_MAP = {
        "contact.creation": "profile_updated",
        "contact.propertyChange": "profile_updated",
        "contact.deletion": "customer_deleted",
        "deal.creation": "deal_created",
        "deal.propertyChange": "deal_changed",
        "company.propertyChange": "company_updated",
    }

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        return verify_plain(
            secret=secret,
            raw_body=raw_body,
            provided=headers.get(self.signature_header.lower(), ""),
        )

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        items = payload if isinstance(payload, list) else [payload]
        events: list[NormalizedEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            subscription = str(item.get("subscriptionType", ""))
            event_type = self.SUBSCRIPTION_MAP.get(subscription)
            customer_id = first_present(item, "objectId", "portalId")
            if event_type is None or not customer_id:
                continue
            data: dict[str, Any] = {"hubspot_subscription": subscription}
            if item.get("propertyName"):
                data[str(item["propertyName"])] = item.get("propertyValue")
            events.append(
                NormalizedEvent(
                    customer_id=str(customer_id),
                    event_type=event_type,
                    data=data,
                    external_event_id=str(item.get("eventId") or ""),
                    occurred_at=timestamp_from(item.get("occurredAt")),
                    source="hubspot",
                )
            )
        return events


class GenericIntegration:
    """A signed passthrough for internal systems and anything without a connector."""

    name = "generic"
    signature_header = "X-Signature"

    def verify(self, *, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
        return verify_plain(
            secret=secret,
            raw_body=raw_body,
            provided=headers.get(self.signature_header.lower(), ""),
        )

    def normalize(self, payload: dict[str, Any]) -> Sequence[NormalizedEvent]:
        items = payload.get("events") if isinstance(payload.get("events"), list) else [payload]
        events: list[NormalizedEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            customer_id = first_present(item, "customer_id", "customerId", "user_id", "userId")
            event_type = first_present(item, "event_type", "eventType", "type", "event")
            if not customer_id or not event_type:
                continue
            events.append(
                NormalizedEvent(
                    customer_id=str(customer_id),
                    event_type=str(event_type),
                    data=item.get("data") if isinstance(item.get("data"), dict) else
                    {k: v for k, v in item.items() if k not in
                     {"customer_id", "customerId", "user_id", "userId", "event_type", "eventType",
                      "type", "event", "id", "timestamp", "occurred_at"}},
                    external_event_id=str(first_present(item, "id", "external_event_id") or ""),
                    occurred_at=timestamp_from(first_present(item, "occurred_at", "timestamp")),
                    customer_email=first_present(item, "email", "customer_email"),
                    customer_name=first_present(item, "name", "customer_name"),
                    source="generic",
                )
            )
        return events
