"""Integration connectors: signature verification and payload normalisation."""

from __future__ import annotations

import json
import time

import pytest

import integrations
from common.errors import AuthenticationError, NotFoundError, ValidationError
from integrations.signature import hmac_sha256_base64, hmac_sha256_hex


def signed_generic(body: bytes, secret: str) -> dict[str, str]:
    return {"x-signature": hmac_sha256_hex(secret, body)}


def test_registry_lists_every_provider():
    assert {"stripe", "intercom", "zendesk", "slack", "posthog", "hubspot", "generic"} <= set(
        integrations.PROVIDERS
    )
    assert all(entry["provider"] for entry in integrations.describe())


def test_unknown_provider_is_rejected():
    with pytest.raises(NotFoundError):
        integrations.get("mailchimp-but-not-really")


def test_missing_secret_is_a_configuration_error():
    with pytest.raises(ValidationError):
        integrations.process(
            provider="generic", payload={}, raw_body=b"{}", headers={}, secret=None
        )


def test_invalid_signature_is_rejected():
    body = b'{"customer_id":"c","event_type":"x"}'
    with pytest.raises(AuthenticationError):
        integrations.process(
            provider="generic",
            payload=json.loads(body),
            raw_body=body,
            headers={"x-signature": "deadbeef"},
            secret="shh",
        )


def test_valid_signature_is_accepted():
    body = b'{"customer_id":"cus_1","event_type":"support_message","data":{"message":"hi there"}}'
    events = integrations.process(
        provider="generic",
        payload=json.loads(body),
        raw_body=body,
        headers=signed_generic(body, "shh"),
        secret="shh",
    )
    assert events[0].customer_id == "cus_1"
    assert events[0].source == "generic"


def test_stripe_subscription_change_is_normalised():
    payload = {
        "id": "evt_1",
        "type": "customer.subscription.updated",
        "created": 1789000000,
        "data": {
            "object": {
                "customer": "cus_9",
                "status": "active",
                "currency": "usd",
                "amount_due": 4900,
                "plan": {"nickname": "Starter"},
            },
            "previous_attributes": {"plan": {"nickname": "Pro"}},
        },
    }
    event = integrations.get("stripe").normalize(payload)[0]
    assert event.event_type == "subscription_changed"
    assert event.data["plan"] == "Starter"
    assert event.data["previous_plan"] == "Pro"
    assert event.data["amount"] == 49.0  # minor units converted
    assert event.external_event_id == "evt_1"


def test_stripe_replay_outside_the_tolerance_window_fails():
    integration = integrations.get("stripe")
    body = b'{"id":"evt_1"}'
    old = str(int(time.time()) - 86400)
    signature = hmac_sha256_hex("whsec", f"{old}.".encode() + body)
    assert not integration.verify(
        secret="whsec", raw_body=body, headers={"stripe-signature": f"t={old},v1={signature}"}
    )


def test_stripe_unknown_event_types_are_ignored():
    assert integrations.get("stripe").normalize({"type": "invoice.upcoming", "data": {}}) == []


def test_intercom_message_carries_the_body_and_redacts_pii():
    event = integrations.get("intercom").normalize(
        {
            "topic": "conversation.user.replied",
            "id": "notif_1",
            "data": {
                "item": {
                    "id": "c1",
                    "created_at": 1789000000,
                    "source": {
                        "body": "<p>Email me at john@example.com, Shopify is broken</p>",
                        "author": {"user_id": "u_1", "email": "john@example.com"},
                    },
                }
            },
        }
    )[0]
    assert event.event_type == "support_message"
    assert "Shopify is broken" in event.data["message"]
    assert "john@example.com" not in event.data["message"]  # redacted before storage
    assert "<p>" not in event.data["message"]  # html stripped


def test_zendesk_maps_status_to_event_type():
    integration = integrations.get("zendesk")
    base = {
        "ticket": {
            "id": 55,
            "subject": "Sync down",
            "description": "It fails every time",
            "requester": {"external_id": "cus_9"},
            "updated_at": "2026-09-10T10:00:00Z",
        }
    }
    assert integration.normalize({"ticket": {**base["ticket"], "status": "new"}})[0].event_type == (
        "support_ticket_created"
    )
    assert integration.normalize({"ticket": {**base["ticket"], "status": "solved"}})[0].event_type == (
        "support_ticket_resolved"
    )


def test_zendesk_signature_uses_base64():
    body = b'{"ticket":{}}'
    assert integrations.get("zendesk").verify(
        secret="shh",
        raw_body=body,
        headers={"x-zendesk-webhook-signature": hmac_sha256_base64("shh", body)},
    )


def test_posthog_drops_high_volume_noise():
    integration = integrations.get("posthog")
    assert integration.normalize({"event": "$pageview", "distinct_id": "u1"}) == []
    event = integration.normalize(
        {"event": "Report Exported", "distinct_id": "u1", "properties": {"format": "csv"}}
    )[0]
    assert event.event_type == "report_exported"
    assert event.data["format"] == "csv"


def test_posthog_handles_batches():
    events = integrations.get("posthog").normalize(
        {"batch": [
            {"event": "report_exported", "distinct_id": "u1"},
            {"event": "$pageview", "distinct_id": "u1"},
            {"event": "invite_sent", "distinct_id": "u2"},
        ]}
    )
    assert [event.event_type for event in events] == ["report_exported", "invite_sent"]


def test_hubspot_property_change():
    event = integrations.get("hubspot").normalize(
        [{"subscriptionType": "contact.propertyChange", "objectId": 42,
          "propertyName": "lifecyclestage", "propertyValue": "customer", "eventId": 7}]
    )[0]
    assert event.event_type == "profile_updated"
    assert event.data["lifecyclestage"] == "customer"


def test_slack_ignores_bot_messages():
    integration = integrations.get("slack")
    assert integration.normalize({"event": {"type": "message", "bot_id": "B1", "text": "hi"}}) == []
    event = integration.normalize(
        {"event": {"type": "message", "user": "U1", "text": "Exports are failing", "ts": "1789000000.1"}}
    )[0]
    assert event.data["message"] == "Exports are failing"


def test_generic_accepts_batches_and_passes_data_through():
    events = integrations.get("generic").normalize(
        {"events": [
            {"customer_id": "c1", "event_type": "goal_created", "data": {"goal": "scale"}},
            {"customerId": "c2", "type": "feature_used", "feature": "reports"},
        ]}
    )
    assert events[0].data == {"goal": "scale"}
    assert events[1].event_type == "feature_used"
    assert events[1].data["feature"] == "reports"
