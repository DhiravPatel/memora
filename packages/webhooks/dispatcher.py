"""Turning a domain event into delivery rows.

Emission is a database write, never an HTTP call: the request that caused the event
returns immediately, and the worker owns retries. If nothing is subscribed, emitting costs
one indexed read.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from common.logging import get_logger
from database.repositories import WebhookDeliveryRepository, WebhookEndpointRepository
from webhooks.events import OutboundEvent

logger = get_logger(__name__)


class WebhookDispatcher:
    def __init__(self, session: AsyncSession) -> None:
        self.endpoints = WebhookEndpointRepository(session)
        self.deliveries = WebhookDeliveryRepository(session)

    async def emit(self, event: OutboundEvent) -> list[str]:
        """Queue one delivery per subscribed endpoint. Returns the delivery ids."""
        endpoints = await self.endpoints.active_for_event(event.project_id, event.type.value)
        if not endpoints:
            return []

        envelope = event.envelope()
        delivery_ids: list[str] = []
        for endpoint in endpoints:
            delivery = await self.deliveries.create(
                project_id=event.project_id,
                endpoint_id=endpoint.id,
                event_type=event.type.value,
                event_id=event.id,
                payload=envelope,
            )
            delivery_ids.append(delivery.id)

        logger.info(
            "webhook.emitted",
            project_id=event.project_id,
            event_type=event.type.value,
            endpoints=len(endpoints),
        )
        return delivery_ids

    async def emit_many(self, events: Sequence[OutboundEvent]) -> list[str]:
        delivery_ids: list[str] = []
        for event in events:
            delivery_ids.extend(await self.emit(event))
        return delivery_ids
