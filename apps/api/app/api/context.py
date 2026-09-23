"""The AI context API: what an external agent should know before it answers."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import ApiProject, DBSession, Engine
from app.schemas.query import ContextRequest, ContextResponse
from common.errors import NotFoundError
from database.repositories import CustomerRepository

router = APIRouter(prefix="/v1/memory", tags=["memory"])


@router.post("/context", response_model=ContextResponse)
async def build_context(
    payload: ContextRequest,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
) -> ContextResponse:
    """Retrieve a token-bounded, ranked view of everything relevant to a task."""
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")

    context = await engine.build_context(
        project=project,
        customer=customer,
        query=payload.query,
        task=payload.task,
        limit=payload.limit,
        token_budget=payload.token_budget,
    )
    return ContextResponse(
        customer_context=context.to_dict(),
        prompt_text=context.to_prompt_text() if payload.format == "text" else None,
        token_count=context.token_count,
        truncated=context.truncated,
    )
