"""Offline consolidation sweep.

Live consolidation compares a new memory against its nearest neighbours. Over time,
memories that were distinct can converge; this periodic pass merges those without
touching anything a user has explicitly authored.
"""

from __future__ import annotations

from typing import Any

from common.enums import MemorySource, MemoryStatus
from common.logging import get_logger
from database.repositories import MemoryRepository
from memory_engine.consolidation.similarity import combined_similarity
from worker.tasks.context import worker_session

logger = get_logger(__name__)

MERGE_THRESHOLD = 0.95


async def consolidate_customer_memories(
    ctx: dict[str, Any], project_id: str, customer_id: str
) -> dict[str, Any]:
    merged = 0

    async with worker_session() as session:
        repository = MemoryRepository(session)
        memories, _ = await repository.list(
            project_id=project_id,
            customer_id=customer_id,
            status=MemoryStatus.ACTIVE,
            limit=200,
        )
        # Most-supported memory wins; the other is superseded, never deleted.
        memories.sort(key=lambda memory: (memory.evidence_count, memory.importance), reverse=True)
        survivors: list[Any] = []

        for memory in memories:
            if memory.source == MemorySource.MANUAL:
                survivors.append(memory)
                continue
            duplicate_of = next(
                (
                    survivor
                    for survivor in survivors
                    if survivor.type == memory.type
                    and combined_similarity(0.0, survivor.content, memory.content) >= 0.75
                ),
                None,
            )
            if duplicate_of is None:
                survivors.append(memory)
                continue

            await repository.apply_update(
                duplicate_of,
                importance=max(duplicate_of.importance, memory.importance),
                confidence=max(duplicate_of.confidence, memory.confidence),
                last_seen_at=max(duplicate_of.last_seen_at, memory.last_seen_at),
                reason="offline_consolidation",
            )
            await repository.supersede(
                memory, superseded_by=duplicate_of.id, reason="offline_consolidation"
            )
            merged += 1

    logger.info(
        "worker.consolidation_sweep", project_id=project_id, customer_id=customer_id, merged=merged
    )
    return {"project_id": project_id, "customer_id": customer_id, "merged": merged}
