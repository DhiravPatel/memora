"""Event contracts and what they found (§26 7.1)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text

from common.ids import new_id
from common.time import utcnow
from database.models import Customer, Event, EventContract
from database.repositories.base import BaseRepository

# Refused events kept as samples on an enforcing contract.
RECENT_REJECTIONS = 20


class ContractRepository(BaseRepository):
    async def get(self, *, project_id: str, event_type: str, for_update: bool = False) -> EventContract | None:
        statement = select(EventContract).where(
            EventContract.project_id == project_id, EventContract.event_type == event_type
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def for_types(self, *, project_id: str, event_types: Sequence[str]) -> dict[str, EventContract]:
        """The contracts covering these event types — one read for a batch."""
        wanted = sorted(set(event_types))
        if not wanted:
            return {}
        result = await self.session.execute(
            select(EventContract).where(EventContract.project_id == project_id, EventContract.event_type.in_(wanted))
        )
        return {row.event_type: row for row in result.scalars()}

    async def list(self, *, project_id: str) -> list[EventContract]:
        result = await self.session.execute(
            select(EventContract).where(EventContract.project_id == project_id).order_by(EventContract.event_type)
        )
        return list(result.scalars())

    async def save(
        self, *, project_id: str, event_type: str, definition: dict[str, Any], actor: str | None
    ) -> tuple[EventContract, dict[str, Any] | None]:
        """Create or replace. A change bumps the version; an identical save does not.
        Returns the row and the definition it replaced."""
        row = await self.get(project_id=project_id, event_type=event_type, for_update=True)
        if row is None:
            row = EventContract(
                id=new_id("ctr"),
                project_id=project_id,
                event_type=event_type,
                mode=definition["mode"],
                version=1,
                definition=definition,
                created_by=actor,
                updated_by=actor,
            )
            self.session.add(row)
            await self.session.flush()
            return row, None
        previous = dict(row.definition or {})
        if previous != definition:
            row.definition = definition
            row.mode = definition["mode"]
            row.version = (row.version or 0) + 1
            row.updated_by = actor
            row.updated_at = utcnow()
            await self.session.flush()
        return row, previous

    async def delete(self, row: EventContract) -> None:
        await self.session.delete(row)
        await self.session.flush()

    async def record_rejection(self, row: EventContract, sample: dict[str, Any], *, now: datetime | None = None) -> None:
        now = now or utcnow()
        row.rejected_count = (row.rejected_count or 0) + 1
        row.last_rejected_at = now
        row.recent_rejections = [sample, *(row.recent_rejections or [])][:RECENT_REJECTIONS]
        await self.session.flush()

    async def forget_customer(self, *, project_id: str, customer_ids: Sequence[str]) -> int:
        """Drop the refused-event samples that name a deleted customer. The counts stay —
        they are numbers, not the customer — and the samples are the only copy of what a
        refused event carried."""
        names = {name for name in customer_ids if name}
        removed = 0
        for row in await self.list(project_id=project_id):
            samples = list(row.recent_rejections or [])
            kept = [item for item in samples if item.get("customer_id") not in names]
            if len(kept) != len(samples):
                removed += len(samples) - len(kept)
                row.recent_rejections = kept
        if removed:
            await self.session.flush()
        return removed

    # ------------------------------------------------------------------ reports

    async def report(self, *, project_id: str, event_type: str, since: datetime) -> dict[str, Any]:
        """What the contract found in events received since ``since``: how many were checked,
        how many broke it, and each way they broke it — with the latest value received."""
        params = {"project_id": project_id, "event_type": event_type, "since": since}
        counts = (
            await self.session.execute(
                text(
                    """
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE contract IS NOT NULL) AS checked,
                           count(*) FILTER (WHERE contract IS NOT NULL AND (contract->>'valid')::boolean IS FALSE) AS violating
                    FROM events
                    WHERE project_id = :project_id AND event_type = :event_type AND created_at >= :since
                    """
                ),
                params,
            )
        ).one()
        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT v->>'path' AS path, v->>'rule' AS rule, v->>'expected' AS expected,
                           count(*) AS events,
                           max(e.created_at) AS last_seen_at,
                           (array_agg(v->>'received' ORDER BY e.created_at DESC))[1] AS received,
                           (array_agg(e.id ORDER BY e.created_at DESC))[1] AS latest_event_id
                    FROM events e, jsonb_array_elements(e.contract->'violations') AS v
                    WHERE e.project_id = :project_id AND e.event_type = :event_type
                      AND e.created_at >= :since AND e.contract IS NOT NULL
                    GROUP BY 1, 2, 3
                    ORDER BY 4 DESC, 1
                    LIMIT 50
                    """
                ),
                params,
            )
        ).all()
        recent = (
            await self.session.execute(
                # The customer as the sender named them, like a refusal's sample.
                select(Event.id, Customer.external_id, Event.external_event_id, Event.created_at, Event.contract)
                .join(Customer, Customer.id == Event.customer_id)
                .where(
                    Event.project_id == project_id,
                    Event.event_type == event_type,
                    Event.created_at >= since,
                    Event.contract.is_not(None),
                    Event.contract["valid"].as_boolean().is_(False),
                )
                .order_by(Event.created_at.desc())
                .limit(10)
            )
        ).all()
        return {
            "events": int(counts.total or 0),
            "checked": int(counts.checked or 0),
            "violating": int(counts.violating or 0),
            "violations": [
                {
                    "path": row.path,
                    "rule": row.rule,
                    "expected": row.expected,
                    "received": row.received,
                    "events": int(row.events),
                    "last_seen_at": row.last_seen_at,
                    "latest_event_id": row.latest_event_id,
                }
                for row in rows
            ],
            "recent": [
                {
                    "event_id": row.id,
                    "customer_id": row.external_id,
                    "external_event_id": row.external_event_id,
                    "received_at": row.created_at,
                    "version": (row.contract or {}).get("version"),
                    "violations": (row.contract or {}).get("violations") or [],
                }
                for row in recent
            ],
        }

    async def coverage(self, *, project_id: str, since: datetime) -> list[dict[str, Any]]:
        """Every event type received since ``since``, how often, and how many broke their
        contract — whether or not one exists."""
        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT event_type,
                           count(*) AS events,
                           count(*) FILTER (WHERE contract IS NOT NULL AND (contract->>'valid')::boolean IS FALSE) AS violating,
                           max(created_at) AS last_seen_at
                    FROM events
                    WHERE project_id = :project_id AND created_at >= :since
                    GROUP BY event_type
                    ORDER BY 2 DESC, 1
                    LIMIT 200
                    """
                ),
                {"project_id": project_id, "since": since},
            )
        ).all()
        return [
            {"event_type": row.event_type, "events": int(row.events), "violating": int(row.violating), "last_seen_at": row.last_seen_at}
            for row in rows
        ]

    async def samples(self, *, project_id: str, event_type: str, limit: int = 200) -> list[dict[str, Any]]:
        """Recent payloads of a type — what a draft contract is inferred from."""
        result = await self.session.execute(
            select(Event.data)
            .where(Event.project_id == project_id, Event.event_type == event_type)
            .order_by(Event.created_at.desc())
            .limit(limit)
        )
        return [dict(row[0] or {}) for row in result.all()]

    async def violations_since(self, *, project_id: str, event_type: str, since: datetime | None) -> int:
        conditions = [
            Event.project_id == project_id,
            Event.event_type == event_type,
            Event.contract.is_not(None),
            Event.contract["valid"].as_boolean().is_(False),
        ]
        if since is not None:
            conditions.append(Event.created_at > since)
        return int(await self.session.scalar(select(func.count()).select_from(Event).where(*conditions)) or 0)
