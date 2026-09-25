"""Freshness, for readers (§26 5.5).

How current each memory is, computed when asked from its own evidence — nothing to go stale
itself — and shown through the reader like everything else: a caller sees the freshness of
the memories they may read, and is told how many they may not.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.drift_service import DriftService
from common.enums import MemoryStatus
from common.time import utcnow
from database.models import Customer, Memory, Project
from database.repositories import DriftRepository, MemoryRepository
from memory_engine.freshness import (
    ATTENTION,
    FALLBACK_WINDOW,
    STATES,
    Freshness,
    counts,
    gather,
    windows,
)

# Memories read for one customer's report: more than any customer page shows.
MAX_MEMORIES = 500
# Order of concern, for the memories a person should look at first.
_CONCERN = {"conflicted": 0, "outdated": 1, "stale": 2, "aging": 3, "active": 4}


class FreshnessService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True) -> None:
        self.session = session
        self.cleared = cleared
        self.memories = MemoryRepository(session, cleared=cleared)
        # Contradictions and flags are looked up by id, for memories the reader already has.
        self.system_memories = MemoryRepository(session)
        self.drift = DriftRepository(session)

    async def annotate(self, *, project: Project, memories: list[Memory]) -> dict[str, Freshness]:
        """Freshness for rows the caller already loaded — two queries, whatever the count."""
        return await gather(
            memories,
            project_id=project.id,
            project_settings=project.settings,
            memory_repository=self.system_memories,
            drift_repository=self.drift,
            now=utcnow(),
        )

    async def for_customer(self, *, project: Project, customer: Customer, limit: int = 50) -> dict[str, Any]:
        """Every standing memory's freshness for one customer: the counts, the memories that
        need a person first, and the open drift flags."""
        rows, total = await self.memories.list(
            project_id=project.id, customer_id=customer.id, status=MemoryStatus.ACTIVE, limit=MAX_MEMORIES
        )
        withheld = await self.memories.withheld_count(
            project_id=project.id, customer_id=customer.id, status=MemoryStatus.ACTIVE
        )
        assessed = await self.annotate(project=project, memories=rows)
        tally = counts(assessed.values())
        standing = sum(tally[state] for state in STATES if state not in ("expired", "superseded"))
        attention = sorted(
            (row for row in rows if assessed[row.id].state != "active"),
            key=lambda row: (_CONCERN.get(assessed[row.id].state, 9), -float(row.importance)),
        )
        # A flag on a memory the reader may not see is not shown; the memory is already in
        # ``withheld``.
        flags, _, _ = await DriftService(self.session, cleared=self.cleared).list(
            project=project, customer=customer, status="open", limit=100
        )
        return {
            "customer_id": customer.external_id,
            "counts": tally,
            "total": standing,
            "needs_attention": sum(tally[state] for state in ATTENTION),
            "stale_share": round(tally["stale"] / standing, 4) if standing else None,
            "memories": [memory_freshness(row, assessed[row.id]) for row in attention[:limit]],
            "drift": flags,
            "windows": windows(project.settings),
            "fallback_window": FALLBACK_WINDOW,
            "withheld": withheld,
            "truncated": total > len(rows),
            "computed_at": utcnow(),
        }


def memory_freshness(memory: Memory, freshness: Freshness) -> dict[str, Any]:
    return {
        "id": memory.id,
        "type": str(memory.type),
        "content": memory.content,
        "importance": round(float(memory.importance), 3),
        "confidence": round(float(memory.confidence), 3),
        "last_seen_at": memory.last_seen_at,
        "freshness": freshness.as_dict(),
    }
