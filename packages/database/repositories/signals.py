"""Signal snapshot persistence: one row per customer per day."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from common.ids import new_id
from common.time import utcnow
from database.models import SignalSnapshot
from database.repositories.base import BaseRepository


class SignalSnapshotRepository(BaseRepository):
    async def record(
        self,
        *,
        project_id: str,
        customer_id: str,
        health_score: float,
        churn_risk: float,
        expansion_score: float,
        trajectory: str,
        signals: list[dict[str, Any]],
        at: datetime | None = None,
    ) -> None:
        """Write today's snapshot, replacing any earlier one for the same day.

        Upserting on ``(customer_id, captured_on)`` means the hot path can call this after
        every event without the table growing per event.
        """
        now = at or utcnow()
        statement = insert(SignalSnapshot).values(
            id=new_id("sig"),
            project_id=project_id,
            customer_id=customer_id,
            captured_on=now.date(),
            health_score=health_score,
            churn_risk=churn_risk,
            expansion_score=expansion_score,
            trajectory=trajectory,
            signals=signals,
            captured_at=now,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_signal_snapshots_customer_id_day",
                set_={
                    "health_score": statement.excluded.health_score,
                    "churn_risk": statement.excluded.churn_risk,
                    "expansion_score": statement.excluded.expansion_score,
                    "trajectory": statement.excluded.trajectory,
                    "signals": statement.excluded.signals,
                    "captured_at": statement.excluded.captured_at,
                },
            )
        )

    async def series(
        self, *, project_id: str, customer_id: str, since: date, limit: int = 120
    ) -> list[SignalSnapshot]:
        result = await self.session.execute(
            select(SignalSnapshot)
            .where(
                SignalSnapshot.project_id == project_id,
                SignalSnapshot.customer_id == customer_id,
                SignalSnapshot.captured_on >= since,
            )
            .order_by(SignalSnapshot.captured_on)
            .limit(limit)
        )
        return list(result.scalars())

    async def latest(self, *, project_id: str, customer_id: str) -> SignalSnapshot | None:
        """The most recent snapshot, whenever it was taken."""
        result = await self.session.execute(
            select(SignalSnapshot)
            .where(
                SignalSnapshot.project_id == project_id,
                SignalSnapshot.customer_id == customer_id,
            )
            .order_by(SignalSnapshot.captured_on.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def baseline(
        self, *, project_id: str, customer_id: str, on_or_before: date
    ) -> SignalSnapshot | None:
        """The most recent snapshot at or before a date — the "where they were" reading."""
        result = await self.session.execute(
            select(SignalSnapshot)
            .where(
                SignalSnapshot.project_id == project_id,
                SignalSnapshot.customer_id == customer_id,
                SignalSnapshot.captured_on <= on_or_before,
            )
            .order_by(SignalSnapshot.captured_on.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def baselines_for_customers(
        self, *, project_id: str, customer_ids: Sequence[str], on_or_before: date
    ) -> dict[str, SignalSnapshot]:
        """One baseline per customer in a single query, for the portfolio view."""
        if not customer_ids:
            return {}
        result = await self.session.execute(
            select(SignalSnapshot)
            .where(
                SignalSnapshot.project_id == project_id,
                SignalSnapshot.customer_id.in_(list(customer_ids)),
                SignalSnapshot.captured_on <= on_or_before,
            )
            .order_by(SignalSnapshot.customer_id, SignalSnapshot.captured_on.desc())
        )
        latest: dict[str, SignalSnapshot] = {}
        for snapshot in result.scalars():
            latest.setdefault(snapshot.customer_id, snapshot)
        return latest

    async def delete_older_than(self, *, project_id: str, cutoff: date) -> int:
        result = await self.session.execute(
            delete(SignalSnapshot).where(
                SignalSnapshot.project_id == project_id, SignalSnapshot.captured_on < cutoff
            )
        )
        return int(result.rowcount or 0)

    async def delete_for_customer(self, *, project_id: str, customer_id: str) -> int:
        result = await self.session.execute(
            delete(SignalSnapshot).where(
                SignalSnapshot.project_id == project_id,
                SignalSnapshot.customer_id == customer_id,
            )
        )
        return int(result.rowcount or 0)
