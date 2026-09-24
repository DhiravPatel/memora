"""Agent approvals that nobody answered.

A request for a person's approval has a deadline (``guardrails.approval_ttl_hours``). Past
it, the request lapses: an agent must not act on a yes that came three days late, when the
customer's situation — the thing the person was judging — may have changed. Lapsing is also
done lazily wherever an approval is read, so this job exists for the *listeners*: every
lapsed request is announced on ``agent.approval_decided`` with status ``expired``, so an
agent waiting on a webhook is not left waiting forever.
"""

from __future__ import annotations

from typing import Any

from app.services.guardrail_service import GuardrailService
from common.logging import get_logger
from worker.tasks.context import worker_session

logger = get_logger(__name__)


async def expire_agent_approvals(ctx: dict[str, Any]) -> dict[str, Any]:
    async with worker_session() as session:
        expired = await GuardrailService(session, cleared=True).expire_due()
    if expired:
        logger.info("agent.approvals_expired", count=expired)
    return {"expired": expired}
