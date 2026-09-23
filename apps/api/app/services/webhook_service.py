"""Outbound webhook configuration and operations.

Endpoints are validated before they are stored (no private-network targets in production,
no plain HTTP), secrets are rotatable, and a test delivery can be fired at any time so a
customer can prove the integration works before they depend on it.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction, DeliveryStatus, WebhookEvent
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.settings import get_settings
from database.models import Project, WebhookDelivery, WebhookEndpoint
from database.repositories import (
    AuditRepository,
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
)
from webhooks import OutboundEvent, WebhookDispatcher, new_secret

logger = get_logger(__name__)

MAX_ENDPOINTS = 10


@dataclass(slots=True)
class CreatedEndpoint:
    endpoint: WebhookEndpoint
    secret: str


class WebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.endpoints = WebhookEndpointRepository(session)
        self.deliveries = WebhookDeliveryRepository(session)
        self.dispatcher = WebhookDispatcher(session)
        self.audit = AuditRepository(session)

    # ------------------------------------------------------------------ endpoints

    async def create(
        self,
        *,
        project: Project,
        url: str,
        description: str | None = None,
        event_types: list[str] | None = None,
        actor_id: str | None = None,
    ) -> CreatedEndpoint:
        validate_url(url)
        existing = await self.endpoints.list(project.id)
        if len(existing) >= MAX_ENDPOINTS:
            raise ConflictError(f"A project may have at most {MAX_ENDPOINTS} webhook endpoints.")
        if any(endpoint.url == url.strip() for endpoint in existing):
            raise ConflictError("That URL is already registered for this project.")

        secret = new_secret()
        endpoint = await self.endpoints.create(
            project_id=project.id,
            url=url,
            secret=secret,
            description=description,
            event_types=validate_event_types(event_types or []),
        )
        await self.audit.record(
            action=AuditAction.WEBHOOK_CHANGE,
            actor_type="user" if actor_id else "api_key",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="webhook_endpoint",
            resource_id=endpoint.id,
            metadata={"event": "created", "url": endpoint.url},
        )
        logger.info("webhook.endpoint_created", project_id=project.id, endpoint_id=endpoint.id)
        return CreatedEndpoint(endpoint=endpoint, secret=secret)

    async def list(self, project: Project) -> list[WebhookEndpoint]:
        return await self.endpoints.list(project.id)

    async def get(self, *, project: Project, endpoint_id: str) -> WebhookEndpoint:
        endpoint = await self.endpoints.get(endpoint_id, project.id)
        if endpoint is None:
            raise NotFoundError("Webhook endpoint not found.")
        return endpoint

    async def update(
        self,
        *,
        project: Project,
        endpoint_id: str,
        url: str | None = None,
        description: str | None = None,
        event_types: list[str] | None = None,
        is_active: bool | None = None,
        actor_id: str | None = None,
    ) -> WebhookEndpoint:
        endpoint = await self.get(project=project, endpoint_id=endpoint_id)
        if url is not None:
            validate_url(url)
        updated = await self.endpoints.update(
            endpoint,
            url=url,
            description=description,
            event_types=validate_event_types(event_types) if event_types is not None else None,
            is_active=is_active,
        )
        await self.audit.record(
            action=AuditAction.WEBHOOK_CHANGE,
            actor_type="user" if actor_id else "api_key",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="webhook_endpoint",
            resource_id=endpoint.id,
            metadata={"event": "updated"},
        )
        return updated

    async def rotate_secret(
        self, *, project: Project, endpoint_id: str, actor_id: str | None = None
    ) -> CreatedEndpoint:
        endpoint = await self.get(project=project, endpoint_id=endpoint_id)
        secret = new_secret()
        await self.endpoints.update(endpoint, secret=secret)
        await self.audit.record(
            action=AuditAction.WEBHOOK_CHANGE,
            actor_type="user" if actor_id else "api_key",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="webhook_endpoint",
            resource_id=endpoint.id,
            metadata={"event": "secret_rotated"},
        )
        return CreatedEndpoint(endpoint=endpoint, secret=secret)

    async def delete(
        self, *, project: Project, endpoint_id: str, actor_id: str | None = None
    ) -> None:
        endpoint = await self.get(project=project, endpoint_id=endpoint_id)
        await self.endpoints.delete(endpoint)
        await self.audit.record(
            action=AuditAction.WEBHOOK_CHANGE,
            actor_type="user" if actor_id else "api_key",
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="webhook_endpoint",
            resource_id=endpoint_id,
            metadata={"event": "deleted"},
        )

    # ----------------------------------------------------------------- deliveries

    async def send_test(self, *, project: Project, endpoint_id: str) -> str:
        """Queue a ``customer.health_changed`` sample so a receiver can verify signing."""
        endpoint = await self.get(project=project, endpoint_id=endpoint_id)
        event = OutboundEvent(
            type=WebhookEvent.CUSTOMER_HEALTH_CHANGED,
            project_id=project.id,
            data={
                "test": True,
                "customer": {"id": "cus_test", "external_id": "cus_test", "name": "Test Customer"},
                "previous_band": "healthy",
                "band": "at_risk",
                "score": 34.0,
                "explanation": "This is a test delivery from the Memory Layer dashboard.",
                "factors": [],
            },
        )
        delivery = await self.deliveries.create(
            project_id=project.id,
            endpoint_id=endpoint.id,
            event_type=event.type.value,
            event_id=event.id,
            payload=event.envelope(),
        )
        return delivery.id

    async def list_deliveries(
        self,
        *,
        project: Project,
        endpoint_id: str | None = None,
        status: DeliveryStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WebhookDelivery], int]:
        return await self.deliveries.list(
            project_id=project.id,
            endpoint_id=endpoint_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def retry_delivery(self, *, project: Project, delivery_id: str) -> WebhookDelivery:
        delivery = await self.deliveries.get(delivery_id, project.id)
        if delivery is None:
            raise NotFoundError("Delivery not found.")
        return await self.deliveries.retry(delivery)

    async def stats(self, project: Project) -> dict[str, int]:
        return await self.deliveries.stats(project.id)

    async def emit(self, event: OutboundEvent) -> list[str]:
        return await self.dispatcher.emit(event)


def validate_event_types(event_types: list[str]) -> list[str]:
    allowed = {event.value for event in WebhookEvent}
    cleaned = [item.strip() for item in event_types if item.strip()]
    unknown = [item for item in cleaned if item not in allowed]
    if unknown:
        raise ValidationError(
            f"Unknown event type(s): {', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}."
        )
    return list(dict.fromkeys(cleaned))


def validate_url(url: str) -> None:
    """Reject anything that would turn the webhook sender into an SSRF tool."""
    parsed = urlparse(url.strip())
    production = get_settings().is_production

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("Webhook URL must be http or https.")
    if production and parsed.scheme != "https":
        raise ValidationError("Webhook URL must use https.")
    if not parsed.hostname:
        raise ValidationError("Webhook URL must include a host.")
    if len(url) > 2048:
        raise ValidationError("Webhook URL is too long.")

    if not production:
        return  # localhost receivers are how people develop against this

    try:
        resolved = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ValidationError(f"Could not resolve webhook host '{parsed.hostname}'.") from exc

    for entry in resolved:
        address = ipaddress.ip_address(entry[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValidationError(
                "Webhook URL must point at a public address, not an internal one."
            )
