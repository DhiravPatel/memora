"""Keeping every memory findable: concepts and embeddings, filled in where missing.

A memory is found by retrieval through its indexes — the lexical embedding for the semantic
leg, its concept ids for the concept leg. Every write path fills both as it writes. This
job is the safety net underneath: memories written before concepts existed, and any write
path that did not embed (memories written through the API, corrections and agent session
summaries all went unembedded until §28's fix), are caught up here in batches. When there is
nothing to fill it costs two indexed queries per project.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from common.logging import get_logger
from database.models import Embedding, Memory, Project
from database.repositories import MemoryRepository
from nlp.concepts import concepts_for
from worker.tasks.context import embedder_for, worker_session

logger = get_logger(__name__)

BATCH = 500
MAX_BATCHES = 40


async def backfill_project_concepts(session: Any, project_id: str, *, batches: int = MAX_BATCHES) -> int:
    filled = 0
    for _ in range(batches):
        rows = (
            await session.execute(
                select(Memory.id, Memory.content)
                .where(Memory.project_id == project_id, Memory.concepts.is_(None))
                .limit(BATCH)
            )
        ).all()
        if not rows:
            break
        for memory_id, content in rows:
            await session.execute(
                update(Memory).where(Memory.id == memory_id).values(concepts=concepts_for(content))
            )
        await session.flush()
        filled += len(rows)
    return filled


async def backfill_project_embeddings(
    session: Any, project_id: str, embedder: Any, *, batches: int = MAX_BATCHES
) -> int:
    repository = MemoryRepository(session)
    filled = 0
    for _ in range(batches):
        memories = list(
            (
                await session.execute(
                    select(Memory)
                    .outerjoin(
                        Embedding,
                        (Embedding.memory_id == Memory.id) & (Embedding.model == embedder.model),
                    )
                    .where(Memory.project_id == project_id, Embedding.id.is_(None))
                    .limit(BATCH)
                )
            ).scalars()
        )
        if not memories:
            break
        for memory in memories:
            await repository.embed(memory, embedder)
        await session.flush()
        filled += len(memories)
    return filled


async def backfill_memory_indexes(ctx: dict[str, Any], project_id: str) -> dict[str, Any]:
    async with worker_session() as session:
        concepts = await backfill_project_concepts(session, project_id)
        embeddings = await backfill_project_embeddings(session, project_id, embedder_for(ctx))
    if concepts or embeddings:
        logger.info("indexes.backfilled", project_id=project_id, concepts=concepts, embeddings=embeddings)
    return {"project_id": project_id, "concepts": concepts, "embeddings": embeddings}


async def backfill_memory_indexes_all(ctx: dict[str, Any]) -> dict[str, Any]:
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
    for project_id in project_ids:
        await ctx["redis"].enqueue_job("backfill_memory_indexes", project_id)
    return {"projects": len(project_ids)}
