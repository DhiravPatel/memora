"""Snapshots of a customer's facts, and their lifecycle history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from common.ids import new_id
from common.time import utcnow
from database.models import CustomerSnapshot, CustomerState
from database.repositories.base import BaseRepository


class CustomerSnapshotRepository(BaseRepository):
    async def latest(self, *, project_id: str, customer_id: str) -> CustomerSnapshot | None:
        result = await self.session.execute(
            select(CustomerSnapshot)
            .where(
                CustomerSnapshot.project_id == project_id,
                CustomerSnapshot.customer_id == customer_id,
            )
            .order_by(CustomerSnapshot.taken_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def at(
        self, *, project_id: str, customer_id: str, moment: datetime
    ) -> CustomerSnapshot | None:
        """What we knew at ``moment``: the newest snapshot taken at or before it."""
        result = await self.session.execute(
            select(CustomerSnapshot)
            .where(
                CustomerSnapshot.project_id == project_id,
                CustomerSnapshot.customer_id == customer_id,
                CustomerSnapshot.taken_at <= moment,
            )
            .order_by(CustomerSnapshot.taken_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get(self, snapshot_id: str, project_id: str) -> CustomerSnapshot | None:
        result = await self.session.execute(
            select(CustomerSnapshot).where(
                CustomerSnapshot.id == snapshot_id, CustomerSnapshot.project_id == project_id
            )
        )
        return result.scalar_one_or_none()

    async def chronological(
        self, *, project_id: str, customer_id: str, limit: int = 2000
    ) -> list[CustomerSnapshot]:
        """A customer's snapshots, oldest first — the newest ``limit`` of them."""
        result = await self.session.execute(
            select(CustomerSnapshot)
            .where(CustomerSnapshot.project_id == project_id, CustomerSnapshot.customer_id == customer_id)
            .order_by(CustomerSnapshot.taken_at.desc())
            .limit(limit)
        )
        return sorted(result.scalars(), key=lambda row: row.taken_at)

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CustomerSnapshot], int]:
        conditions = [
            CustomerSnapshot.project_id == project_id,
            CustomerSnapshot.customer_id == customer_id,
        ]
        if since is not None:
            conditions.append(CustomerSnapshot.taken_at >= since)
        if until is not None:
            conditions.append(CustomerSnapshot.taken_at <= until)
        total = await self.session.scalar(
            select(func.count()).select_from(CustomerSnapshot).where(*conditions)
        )
        result = await self.session.execute(
            select(CustomerSnapshot)
            .where(*conditions)
            .order_by(CustomerSnapshot.taken_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def record(
        self,
        *,
        project_id: str,
        customer_id: str,
        fingerprint: str,
        facts: dict[str, Any],
        redacted_facts: dict[str, Any] | None,
        changes: list[dict[str, Any]],
        reason: str,
        event_id: str | None = None,
        taken_at: datetime | None = None,
        indexed: dict[str, Any] | None = None,
    ) -> CustomerSnapshot:
        indexed = indexed or {}
        snapshot = CustomerSnapshot(
            id=new_id("snp"),
            project_id=project_id,
            customer_id=customer_id,
            taken_at=taken_at or utcnow(),
            reason=reason,
            event_id=event_id,
            fingerprint=fingerprint,
            health_score=indexed.get("health_score"),
            health_band=indexed.get("health_band"),
            state=indexed.get("state"),
            plan=indexed.get("plan"),
            trajectory=indexed.get("trajectory"),
            open_problems=int(indexed.get("open_problems") or 0),
            churn_risk=indexed.get("churn_risk"),
            expansion_score=indexed.get("expansion_score"),
            facts=facts,
            redacted_facts=redacted_facts,
            changes=changes,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot


PRIMARY_TRACK = "lifecycle"


class CustomerStateRepository(BaseRepository):
    """Stays in lifecycle states, one open stay per customer per track (§26 4.2)."""

    async def current(
        self, *, project_id: str, customer_id: str, track: str = PRIMARY_TRACK
    ) -> CustomerState | None:
        result = await self.session.execute(
            select(CustomerState)
            .where(
                CustomerState.project_id == project_id,
                CustomerState.customer_id == customer_id,
                CustomerState.track == track,
                CustomerState.exited_at.is_(None),
            )
            .order_by(CustomerState.entered_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def currents(self, *, project_id: str, customer_id: str) -> dict[str, CustomerState]:
        """The open stay on every track, in one query."""
        result = await self.session.execute(
            select(CustomerState)
            .where(
                CustomerState.project_id == project_id,
                CustomerState.customer_id == customer_id,
                CustomerState.exited_at.is_(None),
            )
            .order_by(CustomerState.entered_at.desc())
        )
        found: dict[str, CustomerState] = {}
        for row in result.scalars():
            found.setdefault(row.track, row)
        return found

    async def entered_between(
        self, *, project_id: str, customer_id: str, since: datetime, until: datetime
    ) -> list[CustomerState]:
        """Every stay that began inside a window, on any track — the transitions in it."""
        result = await self.session.execute(
            select(CustomerState)
            .where(
                CustomerState.project_id == project_id,
                CustomerState.customer_id == customer_id,
                CustomerState.entered_at >= since,
                CustomerState.entered_at <= until,
            )
            .order_by(CustomerState.entered_at.asc())
        )
        return list(result.scalars())

    async def at(
        self, *, project_id: str, customer_id: str, moment: datetime
    ) -> dict[str, CustomerState]:
        """The stay on every track that was open at ``moment``."""
        result = await self.session.execute(
            select(CustomerState)
            .where(
                CustomerState.project_id == project_id,
                CustomerState.customer_id == customer_id,
                CustomerState.entered_at <= moment,
                (CustomerState.exited_at.is_(None)) | (CustomerState.exited_at > moment),
            )
            .order_by(CustomerState.entered_at.desc())
        )
        found: dict[str, CustomerState] = {}
        for row in result.scalars():
            found.setdefault(row.track, row)
        return found

    async def history(
        self,
        *,
        project_id: str,
        customer_id: str,
        track: str | None = PRIMARY_TRACK,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CustomerState], int]:
        """``track=None`` is every track, interleaved by time."""
        conditions = [
            CustomerState.project_id == project_id,
            CustomerState.customer_id == customer_id,
        ]
        if track is not None:
            conditions.append(CustomerState.track == track)
        total = await self.session.scalar(
            select(func.count()).select_from(CustomerState).where(*conditions)
        )
        result = await self.session.execute(
            select(CustomerState)
            .where(*conditions)
            .order_by(CustomerState.entered_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def enter(
        self,
        *,
        project_id: str,
        customer_id: str,
        state: str,
        source: str,
        track: str = PRIMARY_TRACK,
        transition: str | None = None,
        reason: str | None = None,
        evaluation: dict[str, Any] | None = None,
        evidence: list[str] | None = None,
        pinned: bool = False,
        pinned_until: datetime | None = None,
        actor_id: str | None = None,
        snapshot_id: str | None = None,
        at: datetime | None = None,
    ) -> CustomerState:
        """Close the current stay and open a new one, atomically within the session."""
        moment = at or utcnow()
        previous = await self.current(project_id=project_id, customer_id=customer_id, track=track)
        if previous is not None:
            await self.session.execute(
                update(CustomerState)
                .where(CustomerState.id == previous.id)
                .values(exited_at=moment)
            )
        row = CustomerState(
            id=new_id("cst"),
            project_id=project_id,
            customer_id=customer_id,
            track=track,
            state=state,
            previous_state=previous.state if previous else None,
            entered_at=moment,
            source=source,
            transition=transition,
            reason=reason,
            evaluation=evaluation or {},
            evidence=list(evidence or []),
            pinned=pinned,
            pinned_until=pinned_until,
            actor_id=actor_id,
            snapshot_id=snapshot_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def release_pin(self, row: CustomerState) -> CustomerState:
        await self.session.execute(
            update(CustomerState)
            .where(CustomerState.id == row.id)
            .values(pinned=False, pinned_until=None)
        )
        row.pinned = False
        row.pinned_until = None
        return row

    async def counts_by_state(self, project_id: str, track: str = PRIMARY_TRACK) -> dict[str, int]:
        result = await self.session.execute(
            select(CustomerState.state, func.count())
            .where(
                CustomerState.project_id == project_id,
                CustomerState.track == track,
                CustomerState.exited_at.is_(None),
            )
            .group_by(CustomerState.state)
        )
        return {state: int(count) for state, count in result}

    async def customer_ids_in_state(
        self,
        *,
        project_id: str,
        state: str,
        track: str = PRIMARY_TRACK,
        limit: int = 200,
        offset: int = 0,
    ) -> list[str]:
        result = await self.session.execute(
            select(CustomerState.customer_id)
            .where(
                CustomerState.project_id == project_id,
                CustomerState.track == track,
                CustomerState.state == state,
                CustomerState.exited_at.is_(None),
            )
            .order_by(CustomerState.entered_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars())
