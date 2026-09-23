"""Embedding backfill.

Used after an embedding model change, or to repair memories whose embedding write failed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.logging import get_logger
from database.models import Embedding, Memory
from database.repositories import MemoryRepository
from worker.tasks.context import embedder_for, worker_session

logger = get_logger(__name__)


async def generate_embeddings(
    ctx: dict[str, Any], project_id: str, limit: int = 200
) -> dict[str, Any]:
    embedder = embedder_for(ctx)
    async with worker_session() as session:
        repository = MemoryRepository(session)
        missing = await session.execute(
            select(Memory)
            .outerjoin(
                Embedding,
                (Embedding.memory_id == Memory.id) & (Embedding.model == embedder.model),
            )
            .where(Memory.project_id == project_id, Embedding.id.is_(None))
            .limit(limit)
        )
        memories = list(missing.scalars())
        for memory in memories:
            vector = await embedder.embed_one(memory.content)
            await repository.upsert_embedding(
                project_id=project_id,
                memory_id=memory.id,
                vector=vector,
                model=embedder.model,
            )
        logger.info("worker.embeddings_backfilled", project_id=project_id, count=len(memories))
        return {"project_id": project_id, "embedded": len(memories)}
