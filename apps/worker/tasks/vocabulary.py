"""Mining each project's own vocabulary.

Runs nightly, after the summaries. Reads a project's memories, finds the term pairs that
travel together far more often than chance, and replaces the stored mined table. Retrieval
picks the new table up on the next request — there is no cache to invalidate because the
engine reads it per request.

What a person decided is never overwritten: curated glossary entries survive the run, and a
pair somebody rejected is not learned again however strongly the corpus argues for it.

The cost is bounded by ``CORPUS_LIMIT``: pair counting is quadratic in the terms *within*
a memory (small) and linear in the number of memories (capped), so a project with a
million memories still finishes in seconds on the most recent slice.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.enums import MemoryStatus, MemoryType
from common.logging import get_logger
from common.time import utcnow
from database.models import Project
from database.repositories import MemoryRepository, VocabularyRepository
from nlp.mining import mine, terms_of
from worker.tasks.context import worker_session

logger = get_logger(__name__)

# Enough memories to learn a vocabulary from; more adds little and costs linearly.
CORPUS_LIMIT = 5000
MIN_CORPUS = 25


async def mine_project_vocabulary(session: Any, project_id: str) -> dict[str, Any]:
    """The work itself, against a caller-supplied session.

    Separated from the job so it can be driven by a test (or a script) without borrowing
    the worker's global engine, which belongs to a different event loop.
    """
    memories, _ = await MemoryRepository(session).list(
        project_id=project_id, status=MemoryStatus.ACTIVE, limit=CORPUS_LIMIT
    )
    corpus = [
        terms_of(memory.content)
        for memory in memories
        # Summaries are built *from* the other memories; counting them would double
        # every pair they mention and inflate its support.
        if str(memory.type) != MemoryType.SUMMARY.value
    ]
    corpus = [terms for terms in corpus if len(terms) >= 2]

    if len(corpus) < MIN_CORPUS:
        logger.info("vocabulary.too_small", project_id=project_id, memories=len(corpus))
        return {"project_id": project_id, "learned": 0, "reason": "not enough memories"}

    pairs = mine(corpus)
    written = await VocabularyRepository(session).replace_mined(
        project_id=project_id,
        pairs=[(pair.term, pair.synonym, pair.score, pair.support) for pair in pairs],
        mined_at=utcnow(),
    )

    logger.info(
        "vocabulary.mined", project_id=project_id, memories=len(corpus), learned=written
    )
    return {
        "project_id": project_id,
        "learned": written,
        "memories": len(corpus),
        "examples": [pair.as_dict() for pair in pairs[:5]],
    }


async def mine_vocabulary(ctx: dict[str, Any], project_id: str) -> dict[str, Any]:
    """Rebuild one project's mined synonym table."""
    async with worker_session() as session:
        return await mine_project_vocabulary(session, project_id)


async def mine_vocabulary_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly fan-out: one mining job per project."""
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
    for project_id in project_ids:
        await ctx["redis"].enqueue_job("mine_vocabulary", project_id)
    return {"projects": len(project_ids)}
