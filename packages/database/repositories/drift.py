"""Drift flags (§26 5.5): open, then confirmed, kept, dismissed or cleared.

* ``confirmed`` — a person agreed with the evidence; the change was written.
* ``kept`` — a person vouched for the memory; it was confirmed (new evidence).
* ``dismissed`` — a person set the evidence aside; the memory is unchanged.
* ``cleared`` — the system closed it: the evidence stopped holding, or the memory went.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from common.ids import new_id
from common.time import utcnow
from database.models import MemoryDrift
from database.repositories.base import BaseRepository

OPEN, CONFIRMED, KEPT, DISMISSED, CLEARED = "open", "confirmed", "kept", "dismissed", "cleared"
STATUSES = (OPEN, CONFIRMED, KEPT, DISMISSED, CLEARED)
# A person decided the memory stands: only evidence newer than that decision counts again.
SET_ASIDE = (KEPT, DISMISSED)


class DriftRepository(BaseRepository):
    async def get(self, drift_id: str, project_id: str, *, for_update: bool = False) -> MemoryDrift | None:
        statement = select(MemoryDrift).where(MemoryDrift.id == drift_id, MemoryDrift.project_id == project_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def open_for_customer(self, *, project_id: str, customer_id: str) -> list[MemoryDrift]:
        result = await self.session.execute(
            select(MemoryDrift)
            .where(
                MemoryDrift.project_id == project_id,
                MemoryDrift.customer_id == customer_id,
                MemoryDrift.status == OPEN,
            )
            .order_by(MemoryDrift.detected_at.desc())
        )
        return list(result.scalars())

    async def open_for_memories(self, project_id: str, memory_ids: Sequence[str]) -> dict[str, list[MemoryDrift]]:
        if not memory_ids:
            return {}
        result = await self.session.execute(
            select(MemoryDrift).where(
                MemoryDrift.project_id == project_id,
                MemoryDrift.memory_id.in_(list(memory_ids)),
                MemoryDrift.status == OPEN,
            )
        )
        found: dict[str, list[MemoryDrift]] = {}
        for row in result.scalars():
            found.setdefault(row.memory_id, []).append(row)
        return found

    async def last_dismissals(self, *, project_id: str, customer_id: str) -> dict[tuple[str, str], datetime]:
        """When a person last decided each (memory, kind) stands — kept or dismissed — so only
        newer evidence counts."""
        result = await self.session.execute(
            select(MemoryDrift.memory_id, MemoryDrift.kind, func.max(MemoryDrift.resolved_at))
            .where(
                MemoryDrift.project_id == project_id,
                MemoryDrift.customer_id == customer_id,
                MemoryDrift.status.in_(SET_ASIDE),
            )
            .group_by(MemoryDrift.memory_id, MemoryDrift.kind)
        )
        return {(memory_id, kind): at for memory_id, kind, at in result.all() if at is not None}

    async def list(
        self,
        *,
        project_id: str,
        status: str | None = OPEN,
        kind: str | None = None,
        customer_id: str | None = None,
        memory_ids: Sequence[str] | None = None,
        exclude_memory_ids: Sequence[str] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryDrift], int]:
        conditions: list[Any] = [MemoryDrift.project_id == project_id]
        if exclude_memory_ids:
            conditions.append(MemoryDrift.memory_id.not_in(list(exclude_memory_ids)))
        if status:
            conditions.append(MemoryDrift.status == status)
        if kind:
            conditions.append(MemoryDrift.kind == kind)
        if customer_id:
            conditions.append(MemoryDrift.customer_id == customer_id)
        if memory_ids is not None:
            conditions.append(MemoryDrift.memory_id.in_(list(memory_ids)))
        total = await self.session.scalar(select(func.count()).select_from(MemoryDrift).where(*conditions))
        result = await self.session.execute(
            select(MemoryDrift)
            .where(*conditions)
            .order_by(MemoryDrift.detected_at.desc(), MemoryDrift.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def upsert_open(
        self,
        *,
        project_id: str,
        customer_id: str,
        memory_id: str,
        kind: str,
        stated: str,
        observed: str | None,
        summary: str,
        counts: dict[str, Any],
        evidence: list[str],
        since: datetime,
    ) -> tuple[MemoryDrift, bool]:
        """The open flag for (memory, kind), refreshed with the latest counts — or a new one.
        Returns the flag and whether it was newly opened."""
        now = utcnow()
        existing = (
            await self.session.execute(
                select(MemoryDrift)
                .where(MemoryDrift.memory_id == memory_id, MemoryDrift.kind == kind, MemoryDrift.status == OPEN)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.stated = stated
            existing.observed = observed
            existing.summary = summary
            existing.counts = counts
            existing.evidence = evidence
            existing.since = since
            existing.updated_at = now
            await self.session.flush()
            return existing, False
        record = MemoryDrift(
            id=new_id("drf"),
            project_id=project_id,
            customer_id=customer_id,
            memory_id=memory_id,
            kind=kind,
            status=OPEN,
            stated=stated,
            observed=observed,
            summary=summary,
            counts=counts,
            evidence=evidence,
            since=since,
            detected_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self.session.flush()
        return record, True

    async def resolve(
        self,
        record: MemoryDrift,
        *,
        status: str,
        by: str | None,
        by_type: str,
        note: str | None = None,
        replacement_memory_id: str | None = None,
    ) -> MemoryDrift:
        now = utcnow()
        record.status = status
        record.resolved_at = now
        record.updated_at = now
        record.resolved_by = by
        record.resolved_by_type = by_type
        record.note = note
        record.replacement_memory_id = replacement_memory_id
        await self.session.flush()
        return record

    async def counts(self, project_id: str) -> dict[str, dict[str, int]]:
        """Flags by status, then kind: {"open": {"channel": 3, …}, …}."""
        result = await self.session.execute(
            select(MemoryDrift.status, MemoryDrift.kind, func.count())
            .where(MemoryDrift.project_id == project_id)
            .group_by(MemoryDrift.status, MemoryDrift.kind)
        )
        found: dict[str, dict[str, int]] = {}
        for status, kind, count in result.all():
            found.setdefault(status, {})[kind] = int(count)
        return found

    async def detected_between(
        self, *, project_id: str, customer_id: str, since: datetime, until: datetime
    ) -> list[MemoryDrift]:
        """Flags raised in a window, whatever became of them — for "what changed"."""
        result = await self.session.execute(
            select(MemoryDrift)
            .where(
                MemoryDrift.project_id == project_id,
                MemoryDrift.customer_id == customer_id,
                MemoryDrift.detected_at >= since,
                MemoryDrift.detected_at <= until,
            )
            .order_by(MemoryDrift.detected_at.desc())
        )
        return list(result.scalars())

    async def move_customer(self, *, project_id: str, source_customer_id: str, target_customer_id: str) -> int:
        """A merge keeps the flags with the memories they are about."""
        result = await self.session.execute(
            update(MemoryDrift)
            .where(MemoryDrift.project_id == project_id, MemoryDrift.customer_id == source_customer_id)
            .values(customer_id=target_customer_id)
        )
        return int(result.rowcount or 0)
