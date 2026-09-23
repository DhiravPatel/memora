"""Webhook endpoints and their delivery log."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from common.enums import DeliveryStatus
from common.ids import new_id
from common.time import utcnow
from database.models import MAX_CONSECUTIVE_FAILURES, WebhookDelivery, WebhookEndpoint
from database.repositories.base import BaseRepository

# Retry schedule: fast twice, then back off to hours. Matches what receivers expect.
RETRY_DELAYS_SECONDS = (30, 120, 600, 3600, 10800, 21600)
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1


class WebhookEndpointRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        url: str,
        secret: str,
        description: str | None = None,
        event_types: Sequence[str] = (),
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            id=new_id("whe"),
            project_id=project_id,
            url=url.strip(),
            secret=secret,
            description=description,
            event_types=list(event_types),
        )
        self.session.add(endpoint)
        await self.session.flush()
        return endpoint

    async def get(self, endpoint_id: str, project_id: str) -> WebhookEndpoint | None:
        result = await self.session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id, WebhookEndpoint.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def list(self, project_id: str) -> list[WebhookEndpoint]:
        result = await self.session.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.project_id == project_id)
            .order_by(WebhookEndpoint.created_at.desc())
        )
        return list(result.scalars())

    async def active_for_event(self, project_id: str, event_type: str) -> list[WebhookEndpoint]:
        result = await self.session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.project_id == project_id,
                WebhookEndpoint.is_active.is_(True),
            )
        )
        return [endpoint for endpoint in result.scalars() if endpoint.wants(event_type)]

    async def update(
        self,
        endpoint: WebhookEndpoint,
        *,
        url: str | None = None,
        description: str | None = None,
        event_types: Sequence[str] | None = None,
        is_active: bool | None = None,
        secret: str | None = None,
    ) -> WebhookEndpoint:
        if url is not None:
            endpoint.url = url.strip()
        if description is not None:
            endpoint.description = description
        if event_types is not None:
            endpoint.event_types = list(event_types)
        if is_active is not None:
            endpoint.is_active = is_active
            if is_active:
                endpoint.consecutive_failures = 0
        if secret is not None:
            endpoint.secret = secret
        await self.session.flush()
        return endpoint

    async def record_success(self, endpoint: WebhookEndpoint) -> None:
        endpoint.consecutive_failures = 0
        endpoint.last_success_at = utcnow()
        endpoint.last_error = None
        await self.session.flush()

    async def record_failure(self, endpoint: WebhookEndpoint, error: str) -> None:
        endpoint.consecutive_failures += 1
        endpoint.last_failure_at = utcnow()
        endpoint.last_error = error[:1000]
        # An endpoint that has failed this many times in a row is almost certainly gone.
        if endpoint.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            endpoint.is_active = False
        await self.session.flush()

    async def delete(self, endpoint: WebhookEndpoint) -> None:
        await self.session.delete(endpoint)


class WebhookDeliveryRepository(BaseRepository):
    async def create(
        self,
        *,
        project_id: str,
        endpoint_id: str,
        event_type: str,
        event_id: str,
        payload: dict[str, Any],
        scheduled_at: datetime | None = None,
    ) -> WebhookDelivery:
        now = utcnow()
        delivery = WebhookDelivery(
            id=new_id("whd"),
            project_id=project_id,
            endpoint_id=endpoint_id,
            event_type=event_type,
            event_id=event_id,
            payload=payload,
            status=DeliveryStatus.PENDING,
            scheduled_at=scheduled_at or now,
            created_at=now,
        )
        self.session.add(delivery)
        await self.session.flush()
        return delivery

    async def due(self, *, limit: int = 100) -> list[WebhookDelivery]:
        """Pending deliveries whose backoff has elapsed, oldest first."""
        result = await self.session.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == DeliveryStatus.PENDING,
                WebhookDelivery.scheduled_at <= utcnow(),
            )
            .order_by(WebhookDelivery.scheduled_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def mark_succeeded(
        self, delivery: WebhookDelivery, *, status_code: int, body: str, duration_ms: int
    ) -> None:
        delivery.status = DeliveryStatus.SUCCEEDED
        delivery.attempts += 1
        delivery.response_status = status_code
        delivery.response_body = body[:2000]
        delivery.duration_ms = duration_ms
        delivery.delivered_at = utcnow()
        delivery.error = None
        await self.session.flush()

    async def mark_failed(
        self,
        delivery: WebhookDelivery,
        *,
        error: str,
        status_code: int | None = None,
        body: str | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        """Record an attempt. Returns True when the delivery will be retried."""
        delivery.attempts += 1
        delivery.response_status = status_code
        delivery.response_body = (body or "")[:2000] or None
        delivery.error = error[:2000]
        delivery.duration_ms = duration_ms

        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            await self.session.flush()
            return False

        delay = RETRY_DELAYS_SECONDS[min(delivery.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        delivery.status = DeliveryStatus.PENDING
        delivery.scheduled_at = utcnow() + timedelta(seconds=delay)
        await self.session.flush()
        return True

    async def list(
        self,
        *,
        project_id: str,
        endpoint_id: str | None = None,
        status: DeliveryStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WebhookDelivery], int]:
        conditions = [WebhookDelivery.project_id == project_id]
        if endpoint_id:
            conditions.append(WebhookDelivery.endpoint_id == endpoint_id)
        if status:
            conditions.append(WebhookDelivery.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(WebhookDelivery).where(*conditions)
        )
        result = await self.session.execute(
            select(WebhookDelivery)
            .where(*conditions)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def retry(self, delivery: WebhookDelivery) -> WebhookDelivery:
        delivery.status = DeliveryStatus.PENDING
        delivery.scheduled_at = utcnow()
        delivery.attempts = 0
        delivery.error = None
        await self.session.flush()
        return delivery

    async def get(self, delivery_id: str, project_id: str) -> WebhookDelivery | None:
        result = await self.session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id, WebhookDelivery.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def stats(self, project_id: str) -> dict[str, int]:
        result = await self.session.execute(
            select(WebhookDelivery.status, func.count())
            .where(WebhookDelivery.project_id == project_id)
            .group_by(WebhookDelivery.status)
        )
        return {str(status): int(count) for status, count in result}
