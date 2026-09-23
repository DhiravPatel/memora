"""Outbound webhook signing and payload envelopes."""

from __future__ import annotations

import json
import time

import pytest

from common.enums import WebhookEvent
from webhooks import (
    OutboundEvent,
    customer_deleted,
    customer_health_changed,
    memory_created,
    new_secret,
    sign,
    verify,
)
from webhooks.signature import DEFAULT_TOLERANCE_SECONDS

BODY = b'{"id":"evn_1","type":"memory.created"}'


def test_signature_roundtrip():
    secret = new_secret()
    header, timestamp = sign(secret=secret, body=BODY)
    assert header.startswith("t=") and ",v1=" in header
    assert timestamp <= int(time.time())
    assert verify(secret=secret, body=BODY, header=header)


def test_wrong_secret_is_rejected():
    header, _ = sign(secret="whsec_a", body=BODY)
    assert not verify(secret="whsec_b", body=BODY, header=header)


def test_tampered_body_is_rejected():
    secret = new_secret()
    header, _ = sign(secret=secret, body=BODY)
    assert not verify(secret=secret, body=b'{"id":"evn_1","type":"memory.deleted"}', header=header)


def test_replayed_signature_is_rejected():
    """An old timestamp fails even though the digest itself is valid."""
    secret = new_secret()
    old = int(time.time()) - DEFAULT_TOLERANCE_SECONDS - 60
    header, _ = sign(secret=secret, body=BODY, timestamp=old)
    assert not verify(secret=secret, body=BODY, header=header)
    assert verify(secret=secret, body=BODY, header=header, tolerance_seconds=10_000)


@pytest.mark.parametrize("header", ["", "garbage", "t=1", "v1=abc", "t=abc,v1=def"])
def test_malformed_headers_are_rejected(header):
    assert not verify(secret="whsec_a", body=BODY, header=header)


def test_secrets_are_unique_and_prefixed():
    secrets = {new_secret() for _ in range(20)}
    assert len(secrets) == 20
    assert all(secret.startswith("whsec_") for secret in secrets)


class FakeMemory:
    id = "mem_1"
    type = "problem"
    content = "The Shopify integration keeps failing."
    importance = 0.9
    confidence = 0.95
    status = "active"
    evidence_count = 3
    source_event_ids = ["evt_1", "evt_2"]


class FakeCustomer:
    id = "cus_1"
    external_id = "cus_external"
    name = "John"
    email = "john@example.com"


def test_envelope_shape_is_stable():
    event = memory_created(project_id="prj_1", memory=FakeMemory(), customer=FakeCustomer())
    envelope = event.envelope()

    assert set(envelope) == {"id", "type", "version", "created_at", "project_id", "data"}
    assert envelope["type"] == WebhookEvent.MEMORY_CREATED.value
    assert envelope["data"]["memory"]["id"] == "mem_1"
    assert envelope["data"]["customer"]["external_id"] == "cus_external"
    # The envelope must survive JSON serialisation unchanged: it is what gets signed.
    assert json.loads(json.dumps(envelope, default=str))["id"] == envelope["id"]


def test_health_transition_picks_the_specific_event_type():
    falling = customer_health_changed(
        project_id="prj_1",
        customer=FakeCustomer(),
        previous_band="healthy",
        score=30.0,
        band="critical",
        explanation="…",
        factors=[],
    )
    recovering = customer_health_changed(
        project_id="prj_1",
        customer=FakeCustomer(),
        previous_band="critical",
        score=85.0,
        band="healthy",
        explanation="…",
        factors=[],
    )
    sideways = customer_health_changed(
        project_id="prj_1",
        customer=FakeCustomer(),
        previous_band="healthy",
        score=70.0,
        band="watch",
        explanation="…",
        factors=[],
    )
    assert falling.type is WebhookEvent.CUSTOMER_AT_RISK
    assert recovering.type is WebhookEvent.CUSTOMER_RECOVERED
    assert sideways.type is WebhookEvent.CUSTOMER_HEALTH_CHANGED


def test_deletion_event_carries_counts():
    event = customer_deleted(
        project_id="prj_1", customer_id="cus_1", removed={"memories": 4, "events": 12}
    )
    assert event.data["removed"]["memories"] == 4


def test_event_ids_are_unique():
    events = [
        OutboundEvent(type=WebhookEvent.MEMORY_CREATED, project_id="prj_1", data={})
        for _ in range(10)
    ]
    assert len({event.id for event in events}) == 10
