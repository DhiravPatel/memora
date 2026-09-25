"""Event ingestion and read models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, text, update

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

    async def first_event(self, *, project_id: str, customer_id: str) -> tuple[str, str, datetime] | None:
        """(id, type, occurred_at) of a customer's first event — where their history begins."""
        return await self._edge(project_id, customer_id, first=True)

    async def last_event(self, *, project_id: str, customer_id: str) -> tuple[str, str, datetime] | None:
        """(id, type, occurred_at) of a customer's latest event."""
        return await self._edge(project_id, customer_id, first=False)

    async def _edge(self, project_id: str, customer_id: str, *, first: bool) -> tuple[str, str, datetime] | None:
        order = (Event.occurred_at.asc(), Event.id.asc()) if first else (Event.occurred_at.desc(), Event.id.desc())
        row = (
            await self.session.execute(
                select(Event.id, Event.event_type, Event.occurred_at)
                .where(Event.project_id == project_id, Event.customer_id == customer_id)
                .order_by(*order)
                .limit(1)
            )
        ).first()
        return (row[0], row[1], row[2]) if row is not None else None

    async def occurred(self, project_id: str, event_ids: Sequence[str]) -> dict[str, tuple[datetime, str]]:
        """When each event happened, and its type — to place what it caused in time."""
        wanted = sorted({ident for ident in event_ids if ident})
        found: dict[str, tuple[datetime, str]] = {}
        for start in range(0, len(wanted), 1000):
            result = await self.session.execute(
                select(Event.id, Event.occurred_at, Event.event_type).where(
                    Event.project_id == project_id, Event.id.in_(wanted[start : start + 1000])
                )
            )
            found.update({row[0]: (row[1], row[2]) for row in result.all()})
        return found

    async def first_uses(
        self, *, project_id: str, customer_id: str, limit: int = 200
    ) -> list[tuple[str, str, str, str, datetime, datetime, int, str]]:
        """(kind, key, name, first event id, first used, last used, uses, first event type)
        for every feature and integration a customer's events name — oldest first.

        Read from the events themselves, the way the templates read them: a feature event's
        ``feature``/``feature_name``/``action``, an integration event's ``integration``/
        ``provider``/``app``/``service``. A failed or disconnected integration is not a use.
        """
        result = await self.session.execute(
            text(
                """
                WITH uses AS (
                    SELECT e.id, e.event_type, e.occurred_at,
                           CASE WHEN e.event_type ILIKE ANY (ARRAY['%integration%', '%connector%', '%oauth%'])
                                THEN 'integration' ELSE 'feature' END AS kind,
                           CASE WHEN e.event_type ILIKE ANY (ARRAY['%integration%', '%connector%', '%oauth%'])
                                THEN COALESCE(e.data->>'integration', e.data->>'provider', e.data->>'app', e.data->>'service')
                                ELSE COALESCE(e.data->>'feature', e.data->>'feature_name', e.data->>'action') END AS name
                    FROM events e
                    WHERE e.project_id = :project_id
                      AND e.customer_id = :customer_id
                      AND e.event_type ILIKE ANY (ARRAY['%integration%', '%connector%', '%oauth%', '%feature%', '%action_performed%'])
                      AND NOT (e.event_type ILIKE ANY (ARRAY['%fail%', '%error%', '%disconnect%', '%revoke%']))
                ), named AS (
                    SELECT uses.*, lower(regexp_replace(trim(name), '[_\s-]+', ' ', 'g')) AS key
                    FROM uses
                    WHERE name IS NOT NULL AND trim(name) <> ''
                )
                SELECT kind, key, name, id, occurred_at, last_at, uses, event_type FROM (
                    SELECT DISTINCT ON (kind, key)
                           kind, key, name, id, occurred_at, event_type,
                           count(*) OVER (PARTITION BY kind, key) AS uses,
                           max(occurred_at) OVER (PARTITION BY kind, key) AS last_at
                    FROM named
                    ORDER BY kind, key, occurred_at, id
                ) firsts
                ORDER BY occurred_at
                LIMIT CAST(:limit AS integer)
                """
            ),
            {"project_id": project_id, "customer_id": customer_id, "limit": limit},
        )
        return [
            (row.kind, row.key, row.name, row.id, row.occurred_at, row.last_at, int(row.uses), row.event_type)
            for row in result
        ]

    async def quiet_gaps(
        self, *, project_id: str, customer_id: str, min_days: int, limit: int = 100
    ) -> list[tuple[str, datetime, str, datetime, str]]:
        """(last event id, its time, returning event id, its time, its type) for every run
        of at least ``min_days`` without an event — oldest first. A silence still going on
        has no returning event; see :meth:`last_event`."""
        result = await self.session.execute(
            text(
                """
                SELECT previous_id, previous_at, id, occurred_at, event_type FROM (
                    SELECT id, occurred_at, event_type,
                           lag(id) OVER (ORDER BY occurred_at, id) AS previous_id,
                           lag(occurred_at) OVER (ORDER BY occurred_at, id) AS previous_at
                    FROM events
                    WHERE project_id = :project_id AND customer_id = :customer_id
                ) ordered
                WHERE previous_at IS NOT NULL
                  AND occurred_at - previous_at >= make_interval(days => CAST(:min_days AS integer))
                ORDER BY occurred_at
                LIMIT CAST(:limit AS integer)
                """
            ),
            {"project_id": project_id, "customer_id": customer_id, "min_days": min_days, "limit": limit},
        )
        return [(row.previous_id, row.previous_at, row.id, row.occurred_at, row.event_type) for row in result]

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

    async def reaching_out_since(
        self, *, project_id: str, customer_id: str, since: datetime, limit: int = 1000
    ) -> list[tuple[str, str, dict[str, Any], str, datetime]]:
        """(id, type, data, source, occurred_at) for a customer's events since a moment,
        newest first — what drift reads for contacts and billing (§26 5.5). Bounded: a
        pattern shows in the newest thousand."""
        result = await self.session.execute(
            select(Event.id, Event.event_type, Event.data, Event.source, Event.occurred_at)
            .where(
                Event.project_id == project_id,
                Event.customer_id == customer_id,
                Event.occurred_at > since,
            )
            .order_by(Event.occurred_at.desc())
            .limit(limit)
        )
        return [(row[0], row[1], dict(row[2] or {}), row[3], row[4]) for row in result.all()]

    async def last_feature_use(self, *, project_id: str, customer_id: str, feature: str) -> datetime | None:
        """When the customer last used a feature, by the feature events themselves — not the
        memory, which only moves when an event is important enough to be remembered."""
        wanted = " ".join(feature.lower().replace("_", " ").replace("-", " ").split())
        if not wanted:
            return None
        named = func.lower(
            func.replace(
                func.replace(
                    func.coalesce(
                        Event.data["feature"].astext,
                        Event.data["feature_name"].astext,
                        Event.data["action"].astext,
                    ),
                    "_",
                    " ",
                ),
                "-",
                " ",
            )
        )
        return await self.session.scalar(
            select(func.max(Event.occurred_at)).where(
                Event.project_id == project_id,
                Event.customer_id == customer_id,
                or_(Event.event_type.ilike("%feature%"), Event.event_type.ilike("%action_performed%")),
                named == wanted,
            )
        )

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
