"""The delivery worker, against a real HTTP receiver.

Everything else about webhooks can be unit-tested; whether a signed request actually
arrives, verifies, and is recorded correctly can only be proven by sending one.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import database_required

from app.core.security import api_key_prefix, generate_api_key, hash_api_key
from common.enums import DeliveryStatus
from database.base import Base
from database.repositories import (
    OrganizationRepository,
    ProjectRepository,
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
)
from database.session import create_session_factory, dispose_engine, get_engine
from webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    WebhookDispatcher,
    customer_health_changed,
    new_secret,
    verify,
)
from worker.tasks.deliver_webhooks import deliver_webhooks

pytestmark = [pytest.mark.e2e, database_required]

RECEIVED: list[dict] = []


class Receiver(BaseHTTPRequestHandler):
    """Records what arrived, and can be told to fail on demand."""

    fail_next = False

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        RECEIVED.append(
            {
                "path": self.path,
                "body": body,
                "signature": self.headers.get(SIGNATURE_HEADER, ""),
                "event": self.headers.get(EVENT_HEADER, ""),
                "delivery": self.headers.get(DELIVERY_HEADER, ""),
                "content_type": self.headers.get("Content-Type", ""),
                "user_agent": self.headers.get("User-Agent", ""),
            }
        )
        if Receiver.fail_next:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args: object) -> None:  # keep pytest output clean
        return


@pytest.fixture
def receiver():
    RECEIVED.clear()
    Receiver.fail_next = False
    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hooks/memora"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
async def session() -> AsyncSession:
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    factory = create_session_factory()
    async with factory() as db_session:
        yield db_session
    await dispose_engine()


@pytest.fixture
async def project(session: AsyncSession):
    organization = await OrganizationRepository(session).create(name="Hooks", slug="hooks")
    api_key = generate_api_key("test")
    created = await ProjectRepository(session).create(
        organization_id=organization.id,
        name="Hooks",
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key_prefix(api_key),
    )
    await session.commit()
    return created


class FakeCustomer:
    id = "cus_1"
    external_id = "cus_external"
    name = "John"
    email = "john@example.com"


async def queue_health_event(session: AsyncSession, project, *, band: str = "critical") -> str:
    event = customer_health_changed(
        project_id=project.id,
        customer=FakeCustomer(),
        previous_band="healthy",
        score=28.0,
        band=band,
        explanation="Three open problems and a downgrade.",
        factors=[{"key": "open_problems", "label": "3 open problems", "contribution": -21.0}],
    )
    delivery_ids = await WebhookDispatcher(session).emit(event)
    await session.commit()
    return delivery_ids[0] if delivery_ids else ""


async def test_delivery_is_signed_verifiable_and_recorded(session, project, receiver):
    endpoints = WebhookEndpointRepository(session)
    secret = new_secret()
    endpoint = await endpoints.create(
        project_id=project.id, url=receiver, secret=secret, event_types=[]
    )
    await session.commit()

    delivery_id = await queue_health_event(session, project)
    assert delivery_id

    result = await deliver_webhooks({})
    assert result["sent"] == 1, result

    assert len(RECEIVED) == 1
    request = RECEIVED[0]
    assert request["path"] == "/hooks/memora"
    assert request["content_type"] == "application/json"
    assert request["user_agent"].startswith("Memora-Webhooks/")
    assert request["event"] == "customer.at_risk"
    # The receiver can verify the payload with the secret it was given.
    assert verify(secret=secret, body=request["body"], header=request["signature"])
    assert not verify(secret=new_secret(), body=request["body"], header=request["signature"])

    payload = json.loads(request["body"])
    assert payload["type"] == "customer.at_risk"
    assert payload["data"]["band"] == "critical"
    assert payload["data"]["customer"]["external_id"] == "cus_external"
    assert payload["version"]

    delivery = await WebhookDeliveryRepository(session).get(delivery_id, project.id)
    await session.refresh(delivery)
    assert delivery.status == DeliveryStatus.SUCCEEDED
    assert delivery.response_status == 200
    assert delivery.attempts == 1
    assert delivery.duration_ms is not None
    assert delivery.delivered_at is not None

    await session.refresh(endpoint)
    assert endpoint.consecutive_failures == 0
    assert endpoint.last_success_at is not None


async def test_failed_delivery_is_retried_with_backoff(session, project, receiver):
    endpoints = WebhookEndpointRepository(session)
    endpoint = await endpoints.create(
        project_id=project.id, url=receiver, secret=new_secret(), event_types=[]
    )
    await session.commit()

    Receiver.fail_next = True
    delivery_id = await queue_health_event(session, project)
    result = await deliver_webhooks({})
    assert result["failed"] == 1

    deliveries = WebhookDeliveryRepository(session)
    delivery = await deliveries.get(delivery_id, project.id)
    await session.refresh(delivery)
    assert delivery.status == DeliveryStatus.PENDING  # scheduled for another attempt
    assert delivery.attempts == 1
    assert delivery.response_status == 500
    assert "500" in (delivery.error or "")
    assert delivery.scheduled_at > delivery.created_at  # backed off

    await session.refresh(endpoint)
    assert endpoint.consecutive_failures == 1
    assert endpoint.last_error


async def test_unreachable_endpoint_records_a_connection_error(session, project):
    endpoints = WebhookEndpointRepository(session)
    await endpoints.create(
        project_id=project.id,
        url="http://127.0.0.1:9/nothing-listens-here",
        secret=new_secret(),
        event_types=[],
    )
    await session.commit()

    delivery_id = await queue_health_event(session, project)
    result = await deliver_webhooks({})
    assert result["failed"] == 1

    delivery = await WebhookDeliveryRepository(session).get(delivery_id, project.id)
    await session.refresh(delivery)
    assert delivery.response_status is None
    assert "Connection failed" in (delivery.error or "")


async def test_event_type_filter_is_respected(session, project, receiver):
    endpoints = WebhookEndpointRepository(session)
    await endpoints.create(
        project_id=project.id,
        url=receiver,
        secret=new_secret(),
        event_types=["memory.created"],  # deliberately not the health event
    )
    await session.commit()

    delivery_id = await queue_health_event(session, project)
    assert delivery_id == ""  # nothing subscribed, so nothing was queued

    result = await deliver_webhooks({})
    assert result["sent"] == 0
    assert RECEIVED == []


async def test_inactive_endpoint_skips_delivery(session, project, receiver):
    endpoints = WebhookEndpointRepository(session)
    endpoint = await endpoints.create(
        project_id=project.id, url=receiver, secret=new_secret(), event_types=[]
    )
    await session.commit()

    delivery_id = await queue_health_event(session, project)
    await endpoints.update(endpoint, is_active=False)
    await session.commit()

    result = await deliver_webhooks({})
    assert result["skipped"] == 1
    assert RECEIVED == []

    delivery = await WebhookDeliveryRepository(session).get(delivery_id, project.id)
    await session.refresh(delivery)
    assert delivery.status == DeliveryStatus.DISABLED
