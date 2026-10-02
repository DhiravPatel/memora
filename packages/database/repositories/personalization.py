"""Stored personalization documents (§26 6.6)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text

from common.ids import new_id
from common.time import utcnow
from database.models import CustomerPersonalization
from database.repositories.base import BaseRepository


class PersonalizationRepository(BaseRepository):
    async def get(self, *, project_id: str, customer_id: str, for_update: bool = False) -> CustomerPersonalization | None:
        statement = select(CustomerPersonalization).where(
            CustomerPersonalization.project_id == project_id,
            CustomerPersonalization.customer_id == customer_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def many(self, *, project_id: str, customer_ids: Sequence[str]) -> dict[str, CustomerPersonalization]:
        if not customer_ids:
            return {}
        result = await self.session.execute(
            select(CustomerPersonalization).where(
                CustomerPersonalization.project_id == project_id,
                CustomerPersonalization.customer_id.in_(list(customer_ids)),
            )
        )
        return {row.customer_id: row for row in result.scalars()}

    async def save(
        self,
        *,
        project_id: str,
        customer_id: str,
        document: dict[str, Any],
        version: str,
        settings_hash: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[CustomerPersonalization, dict[str, Any] | None, bool]:
        """Store a computation. Returns the row, the document it replaced (``None`` for the
        first), and whether what the product sees changed."""
        now = now or utcnow()
        row = await self.get(project_id=project_id, customer_id=customer_id, for_update=True)
        if row is None:
            row = CustomerPersonalization(
                id=new_id("prs"),
                project_id=project_id,
                customer_id=customer_id,
                document=document,
                version=version,
                settings_hash=settings_hash,
                computed_at=now,
                changed_at=now,
                reason=reason,
            )
            self.session.add(row)
            await self.session.flush()
            return row, None, True
        previous = dict(row.document or {})
        changed = row.version != version
        row.document = document
        row.settings_hash = settings_hash
        row.computed_at = now
        row.reason = reason
        if changed:
            row.version = version
            row.changed_at = now
        await self.session.flush()
        return row, previous, changed

    async def summary(self, *, project_id: str) -> dict[str, Any]:
        """Across the project's customers: how many have each hint on, each experience level
        and mood, and the commonest frictions and relied-on features."""
        params = {"project_id": project_id}
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(CustomerPersonalization).where(CustomerPersonalization.project_id == project_id)
            )
            or 0
        )

        async def pairs(query: str) -> list[tuple[str, int]]:
            rows = (await self.session.execute(text(query), params)).all()
            return [(str(row[0]), int(row[1])) for row in rows if row[0] is not None]

        hints = await pairs(
            "SELECT hint.key, count(*) FILTER (WHERE hint.value = 'true'::jsonb) "
            "FROM customer_personalizations p, jsonb_each(p.document->'ui') AS hint "
            "WHERE p.project_id = :project_id GROUP BY hint.key ORDER BY hint.key"
        )
        experience = await pairs(
            "SELECT document->>'experience', count(*) FROM customer_personalizations "
            "WHERE project_id = :project_id GROUP BY 1 ORDER BY 2 DESC"
        )
        mood = await pairs(
            "SELECT document->>'mood', count(*) FROM customer_personalizations "
            "WHERE project_id = :project_id GROUP BY 1 ORDER BY 2 DESC"
        )
        frictions = await pairs(
            "SELECT friction, count(*) FROM customer_personalizations p, "
            "jsonb_array_elements_text(p.document->'known_frictions') AS friction "
            "WHERE p.project_id = :project_id GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
        )
        relied_on = await pairs(
            "SELECT feature, count(*) FROM customer_personalizations p, "
            "jsonb_array_elements_text(p.document->'relied_on_features') AS feature "
            "WHERE p.project_id = :project_id GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
        )
        oldest = await self.session.scalar(
            select(func.min(CustomerPersonalization.computed_at)).where(CustomerPersonalization.project_id == project_id)
        )
        return {
            "customers": total,
            "hints": dict(hints),
            "experience": dict(experience),
            "mood": dict(mood),
            "frictions": [{"key": key, "customers": count} for key, count in frictions],
            "relied_on_features": [{"key": key, "customers": count} for key, count in relied_on],
            "oldest_computed_at": oldest,
        }
