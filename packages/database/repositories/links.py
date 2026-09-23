"""Memory links: persistence for the causal/temporal graph between memories."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, or_, select

from common.ids import new_id
from common.time import utcnow
from database.models import Memory, MemoryLink
from database.repositories.base import BaseRepository


class MemoryLinkRepository(BaseRepository):
    async def replace_for_customer(
        self,
        *,
        project_id: str,
        customer_id: str,
        links: Sequence[tuple[str, str, str, float, str]],
    ) -> int:
        """Rewrite a customer's inferred links.

        Links are derived, not authored: recomputing them wholesale keeps them consistent
        with the memories as they are *now*, which matters after a consolidation or a
        correction changes the underlying story.
        """
        await self.session.execute(
            delete(MemoryLink).where(
                MemoryLink.project_id == project_id, MemoryLink.customer_id == customer_id
            )
        )
        now = utcnow()
        for source_id, target_id, link_type, confidence, rationale in links:
            self.session.add(
                MemoryLink(
                    id=new_id("mlk"),
                    project_id=project_id,
                    customer_id=customer_id,
                    source_memory_id=source_id,
                    target_memory_id=target_id,
                    link_type=link_type,
                    confidence=confidence,
                    rationale=rationale,
                    created_at=now,
                )
            )
        await self.session.flush()
        return len(links)

    async def for_memory(self, *, project_id: str, memory_id: str) -> list[MemoryLink]:
        result = await self.session.execute(
            select(MemoryLink).where(
                MemoryLink.project_id == project_id,
                or_(
                    MemoryLink.source_memory_id == memory_id,
                    MemoryLink.target_memory_id == memory_id,
                ),
            )
            .order_by(MemoryLink.confidence.desc())
        )
        return list(result.scalars())

    async def for_customer(
        self, *, project_id: str, customer_id: str, link_type: str | None = None, limit: int = 200
    ) -> list[MemoryLink]:
        conditions = [
            MemoryLink.project_id == project_id,
            MemoryLink.customer_id == customer_id,
        ]
        if link_type:
            conditions.append(MemoryLink.link_type == link_type)
        result = await self.session.execute(
            select(MemoryLink)
            .where(*conditions)
            .order_by(MemoryLink.confidence.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def hydrate(
        self, *, project_id: str, links: Sequence[MemoryLink]
    ) -> dict[str, Memory]:
        """Fetch the memories referenced by a set of links, in one query."""
        ids = {link.source_memory_id for link in links} | {link.target_memory_id for link in links}
        if not ids:
            return {}
        result = await self.session.execute(
            select(Memory).where(Memory.project_id == project_id, Memory.id.in_(list(ids)))
        )
        return {memory.id: memory for memory in result.scalars()}

    async def count(self, project_id: str) -> int:
        total = await self.session.scalar(
            select(func.count()).select_from(MemoryLink).where(MemoryLink.project_id == project_id)
        )
        return int(total or 0)
