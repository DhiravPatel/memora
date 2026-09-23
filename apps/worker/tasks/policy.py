"""Re-applying a project's restriction policy to memories written before it.

A policy classifies memories as they are written. Changing it therefore leaves history
behind: turn on "anything mentioning salary is restricted" and yesterday's memories are
still readable by everyone, which is exactly the leak the policy was added to close.

So a settings write enqueues this, and it walks the project's memories in batches,
recomputing sensitivity. Doing it here rather than inside the settings request matters:
a project with a million memories would otherwise hang an HTTP request, and a migration
cannot do it because the policy can change at any time afterwards.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import MemoryStatus, Sensitivity
from common.logging import get_logger
from database.models import Memory
from database.repositories import ProjectRepository
from memory_engine.policy import PolicyError, compile_policy
from worker.tasks.context import worker_session

logger = get_logger(__name__)

BATCH = 500


async def reclassify_project(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """Recompute sensitivity for every memory in a project, on a caller's session.

    Split out from the job so tests — and any operator script — can run the real walk
    rather than a copy of it that drifts.
    """
    restricted = relaxed = scanned = 0

    project = await ProjectRepository(session).get(project_id)
    if project is None:
        return {"project_id": project_id, "status": "missing"}

    try:
        policy = compile_policy((project.settings or {}).get("restriction_policies"))
    except PolicyError as exc:
        # Stored settings are validated on write, so this means something wrote around
        # the API. Leaving sensitivity untouched is the safe failure.
        logger.error("policy.invalid", project_id=project_id, error=str(exc))
        return {"project_id": project_id, "status": "invalid_policy", "error": str(exc)}

    offset = 0
    while True:
        rows = (
            (
                await session.execute(
                    select(Memory)
                    .where(
                        Memory.project_id == project_id,
                        Memory.status != MemoryStatus.DELETED,
                    )
                    .order_by(Memory.id)
                    .limit(BATCH)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            break

        for memory in rows:
            scanned += 1
            verdict = policy.evaluate(content=memory.content, memory_type=str(memory.type))
            wanted = Sensitivity.RESTRICTED if verdict.restricted else Sensitivity.NORMAL
            meta = dict(memory.meta or {})
            reason = meta.get("restricted_by")
            # The reason is checked as well as the verdict: a memory can stay restricted
            # while the rule that restricts it changes name or is replaced by another, and
            # a stale reason is what an auditor reads when they ask why it was hidden.
            if memory.sensitivity == wanted and reason == (verdict.reason if verdict.restricted else None):
                continue
            if memory.sensitivity != wanted:
                restricted += 1 if verdict.restricted else 0
                relaxed += 0 if verdict.restricted else 1
            memory.sensitivity = wanted
            if verdict.restricted:
                meta["restricted_by"] = verdict.reason
            else:
                meta.pop("restricted_by", None)
            memory.meta = meta

        await session.flush()
        offset += BATCH

    logger.info(
        "policy.reclassified",
        project_id=project_id,
        scanned=scanned,
        restricted=restricted,
        relaxed=relaxed,
    )
    return {
        "project_id": project_id,
        "scanned": scanned,
        "restricted": restricted,
        "relaxed": relaxed,
    }


async def reclassify_memories(ctx: dict[str, Any], project_id: str) -> dict[str, Any]:
    """Recompute sensitivity for every memory in a project."""
    async with worker_session() as session:
        return await reclassify_project(session, project_id)
