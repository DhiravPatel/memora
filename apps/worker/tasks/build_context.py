"""Pre-built customer context.

Support and sales agents usually ask about the same customers repeatedly. Warming the
context after a high-importance event keeps the first agent request fast, and the cache
is invalidated by simply overwriting it on the next event.
"""

from __future__ import annotations

import json
from typing import Any

from common.logging import get_logger
from database.repositories import CustomerRepository, ProjectRepository
from worker.tasks.context import engine_for, worker_session

logger = get_logger(__name__)

CACHE_PREFIX = "memory:context"
CACHE_TTL_SECONDS = 900


def cache_key(project_id: str, customer_id: str) -> str:
    return f"{CACHE_PREFIX}:{project_id}:{customer_id}"


async def warm_context(
    ctx: dict[str, Any], project_id: str, customer_id: str, task: str | None = None
) -> dict[str, Any]:
    async with worker_session() as session:
        project = await ProjectRepository(session).get(project_id)
        customer = await CustomerRepository(session).get(customer_id, project_id)
        if project is None or customer is None:
            return {"cached": False, "reason": "project or customer missing"}

        context = await engine_for(ctx, session).build_context(
            project=project, customer=customer, task=task
        )

    payload = json.dumps(context.to_dict(), default=str)
    await ctx["redis"].set(cache_key(project_id, customer_id), payload, ex=CACHE_TTL_SECONDS)
    logger.info(
        "worker.context_warmed",
        project_id=project_id,
        customer_id=customer_id,
        tokens=context.token_count,
    )
    return {"cached": True, "tokens": context.token_count}
