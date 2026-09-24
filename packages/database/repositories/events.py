"""Event ingestion and read models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update

from common.enums import EventStatus
from common.ids import new_id
from common.time import utcnow
from database.models import Event
from database.repositories.base import BaseRepository


class EventRepository(BaseRepository):
    async def get(self, event_id: str, project_id: str) -> Event | None:
        result = await self.session.execute(
            select(Event).where(Event.id == event_id, Event.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_many(self, event_ids: Sequence[str], project_id: str) -> list[Event]:
        if not event_ids:
            return []
        result = await self.session.execute(
            select(Event).where(Event.project_id == project_id, Event.id.in_(list(event_ids)))
        )
        return list(result.scalars())

    async def get_by_external_id(self, external_event_id: str, project_id: str) -> Event | None:
        result = await self.session.execute(
            select(Event).where(
                Event.project_id == project_id,
                Event.external_event_id == external_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        project_id: str,
        customer_id: str,
        event_type: str,
        data: dict[str, Any],
        occurred_at: datetime,
        external_event_id: str | None = None,
        source: str = "api",
        importance: float = 0.0,
        status: EventStatus = EventStatus.PENDING,
    ) -> Event:
        event = Event(
            id=new_id("evt"),
            project_id=project_id,
            customer_id=customer_id,
            event_type=event_type,
            external_event_id=external_event_id,
            data=data,
            source=source,
            importance=importance,
            occurred_at=occurred_at,
            created_at=utcnow(),
            status=status,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def mark_status(
        self,
        event_id: str,
        status: EventStatus,
        *,
        error: str | None = None,
        increment_attempts: bool = False,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "error": error}
        # Only written when there is something to say. A retry that fails must not erase
        # the explanation of the run that succeeded before it.
        if outcome is not None:
            values["outcome"] = outcome
        if status in (EventStatus.PROCESSED, EventStatus.SKIPPED, EventStatus.FAILED):
            values["processed_at"] = utcnow()
        if increment_attempts:
            values["attempts"] = Event.attempts + 1
        await self.session.execute(update(Event).where(Event.id == event_id).values(**values))

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        event_type: str | None = None,
        status: EventStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Event], int]:
        conditions = [Event.project_id == project_id]
        if customer_id:
            conditions.append(Event.customer_id == customer_id)
        if event_type:
            conditions.append(Event.event_type == event_type)
        if status:
            conditions.append(Event.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(Event).where(*conditions)
        )
        result = await self.session.execute(
            select(Event)
            .where(*conditions)
            .order_by(Event.occurred_at.desc(), Event.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def recent_for_customer(
        self, *, project_id: str, customer_id: str, limit: int = 20
    ) -> list[Event]:
        result = await self.session.execute(
            select(Event)
            .where(Event.project_id == project_id, Event.customer_id == customer_id)
            .order_by(Event.occurred_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def changed_since(
        self, *, project_id: str, since: datetime, limit: int = 100
    ) -> list[Event]:
        """Events created or processed after ``since``, oldest first.

        Both columns, because an event changes twice in its life — once when it arrives
        and once when the worker finishes with it — and a live view wants to show both.
        """
        result = await self.session.execute(
            select(Event)
            .where(
                Event.project_id == project_id,
                or_(Event.created_at > since, Event.processed_at > since),
            )
            .order_by(func.greatest(Event.created_at, func.coalesce(Event.processed_at, Event.created_at)))
            .limit(limit)
        )
        return list(result.scalars())

    async def counts_by_customer(
        self, *, project_id: str, customer_ids: Sequence[str]
    ) -> dict[str, int]:
        if not customer_ids:
            return {}
        result = await self.session.execute(
            select(Event.customer_id, func.count())
            .where(Event.project_id == project_id, Event.customer_id.in_(list(customer_ids)))
            .group_by(Event.customer_id)
        )
        return {customer_id: int(count) for customer_id, count in result}

    async def first_occurred_at(self, *, project_id: str, customer_id: str) -> datetime | None:
        """When a customer's first event happened."""
        return await self.session.scalar(
            select(func.min(Event.occurred_at)).where(
                Event.project_id == project_id, Event.customer_id == customer_id
            )
        )

    async def count_between(
        self, *, project_id: str, customer_id: str, since: datetime, until: datetime
    ) -> int:
        """Events a customer produced in ``[since, until)``, by when they happened."""
        total = await self.session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.project_id == project_id,
                Event.customer_id == customer_id,
                Event.occurred_at >= since,
                Event.occurred_at < until,
            )
        )
        return int(total or 0)

    async def window_counts_by_customer(
        self,
        *,
        project_id: str,
        customer_ids: Sequence[str],
        boundary: datetime,
        since: datetime,
    ) -> dict[str, tuple[int, int]]:
        """Events per customer either side of ``boundary``, back as far as ``since``.

        One grouped query rather than two per customer: the signal engine compares a recent
        window against the one before it for every customer in a portfolio view.
        """
        if not customer_ids:
            return {}
        recent = func.count().filter(Event.occurred_at >= boundary)
        prior = func.count().filter(Event.occurred_at < boundary)
        result = await self.session.execute(
            select(Event.customer_id, recent, prior)
            .where(
                Event.project_id == project_id,
                Event.customer_id.in_(list(customer_ids)),
                Event.occurred_at >= since,
            )
            .group_by(Event.customer_id)
        )
        return {
            customer_id: (int(recent_count), int(prior_count))
            for customer_id, recent_count, prior_count in result
        }

    async def type_breakdown(self, *, project_id: str, limit: int = 20) -> list[tuple[str, int]]:
        """Most common event types, for the dashboard and for tuning importance."""
        result = await self.session.execute(
            select(Event.event_type, func.count())
            .where(Event.project_id == project_id)
            .group_by(Event.event_type)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [(event_type, int(count)) for event_type, count in result]

    async def count(self, project_id: str, status: EventStatus | None = None) -> int:
        conditions = [Event.project_id == project_id]
        if status:
            conditions.append(Event.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(Event).where(*conditions)
        )
        return int(total or 0)

    async def delete_older_than(self, *, project_id: str, cutoff: datetime) -> int:
        result = await self.session.execute(
            select(Event.id).where(Event.project_id == project_id, Event.occurred_at < cutoff)
        )
        ids = [row[0] for row in result]
        if not ids:
            return 0
        await self.session.execute(
            Event.__table__.delete().where(Event.id.in_(ids))
        )
        return len(ids)
