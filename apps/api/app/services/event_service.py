"""Event ingestion.

The write path is deliberately small: authenticate, validate, deduplicate, store, queue,
return 202. Anything that can fail slowly (AI calls, consolidation) happens in the worker.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import enqueue
from app.schemas.events import EventAccepted, EventIn
from common.enums import EventStatus
from common.errors import ConflictError, NotFoundError
from common.logging import get_logger
from common.time import ensure_utc, utcnow
from database.models import Project
from database.repositories import CustomerRepository, EventRepository, UsageRepository
from memory_engine.extraction.filters import score_event
from webhooks import WebhookDispatcher, customer_created

logger = get_logger(__name__)

PROCESS_EVENT_JOB = "process_event"


class EventService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events = EventRepository(session)
        self.customers = CustomerRepository(session)
        self.usage = UsageRepository(session)

    async def ingest(
        self, *, project: Project, payload: EventIn, queue: bool = True
    ) -> EventAccepted:
        # Idempotency: the same external_event_id is accepted once and only once.
        if payload.external_event_id:
            existing = await self.events.get_by_external_id(
                payload.external_event_id, project.id
            )
            if existing is not None:
                return EventAccepted(
                    event_id=existing.id,
                    status="duplicate",
                    customer_id=existing.customer_id,
                    importance=existing.importance,
                    queued=False,
                )

        known = await self.customers.get_by_external_id(payload.customer_id, project.id)
        customer = await self.customers.upsert(
            project_id=project.id,
            external_id=payload.customer_id,
            email=payload.customer_email,
            name=payload.customer_name,
        )
        if known is None:
            await WebhookDispatcher(self.session).emit(
                customer_created(project_id=project.id, customer=customer)
            )

        importance = score_event(
            event_type=payload.event_type,
            data=payload.data,
            overrides=(project.settings or {}).get("event_importance") or {},
        )
        occurred_at = ensure_utc(payload.occurred_at) if payload.occurred_at else utcnow()

        event = await self.events.create(
            project_id=project.id,
            customer_id=customer.id,
            event_type=payload.event_type,
            data=payload.data,
            occurred_at=occurred_at,
            external_event_id=payload.external_event_id,
            source=payload.source,
            importance=importance,
            status=EventStatus.PENDING,
        )
        await self.customers.touch_last_event(customer, occurred_at)
        await self.usage.increment(project_id=project.id, metric="events_received")

        queued = False
        if queue:
            # Commit first: the worker must never look for a row that is not there yet.
            await self.session.commit()
            queued = await enqueue(PROCESS_EVENT_JOB, event.id) is not None

        logger.info(
            "event.ingested",
            event_id=event.id,
            project_id=project.id,
            event_type=event.event_type,
            importance=importance,
            queued=queued,
        )
        return EventAccepted(
            event_id=event.id,
            status="accepted",
            customer_id=customer.id,
            importance=importance,
            queued=queued,
        )

    async def retry(self, *, project: Project, event_id: str) -> EventAccepted:
        """Put a failed or stuck event back on the queue."""
        event = await self.events.get(event_id, project.id)
        if event is None:
            raise NotFoundError("Event not found.")
        if event.status == EventStatus.PROCESSED:
            raise ConflictError("This event has already been processed.")

        await self.events.mark_status(event.id, EventStatus.PENDING, error=None)
        await self.session.commit()
        queued = await enqueue(PROCESS_EVENT_JOB, event.id) is not None
        logger.info("event.retry", event_id=event.id, queued=queued)
        return EventAccepted(
            event_id=event.id,
            status="accepted",
            customer_id=event.customer_id,
            importance=event.importance,
            queued=queued,
        )

    async def retry_failed(self, *, project: Project, limit: int = 100) -> int:
        """Drain the dead-letter backlog for a project."""
        events, _ = await self.events.list(
            project_id=project.id, status=EventStatus.FAILED, limit=limit
        )
        for event in events:
            await self.events.mark_status(event.id, EventStatus.PENDING, error=None)
        await self.session.commit()

        requeued = 0
        for event in events:
            if await enqueue(PROCESS_EVENT_JOB, event.id) is not None:
                requeued += 1
        logger.info("event.retry_failed", project_id=project.id, requeued=requeued)
        return requeued

    async def ingest_batch(
        self, *, project: Project, payloads: list[EventIn]
    ) -> tuple[list[EventAccepted], int]:
        accepted: list[EventAccepted] = []
        duplicates = 0
        for payload in payloads:
            result = await self.ingest(project=project, payload=payload, queue=False)
            if result.status == "duplicate":
                duplicates += 1
            accepted.append(result)

        await self.session.commit()
        for result in accepted:
            if result.status == "accepted":
                result.queued = await enqueue(PROCESS_EVENT_JOB, result.event_id) is not None
        return accepted, duplicates
