"""Natural-language memory queries (project API key)."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import ApiProject, DBSession, Engine
from app.schemas.query import (
    EvidenceOut,
    MemoryQueryRequest,
    MemoryQueryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    QueriedMemory,
)
from common.errors import NotFoundError
from database.repositories import CustomerRepository

router = APIRouter(prefix="/v1/memory", tags=["memory"])


@router.post("/query", response_model=MemoryQueryResponse)
async def query_memory(
    payload: MemoryQueryRequest,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
) -> MemoryQueryResponse:
    """Ask a question about a customer and get an evidence-backed answer."""
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")

    result = await engine.answer(
        project=project, customer=customer, query=payload.query, limit=payload.limit
    )
    return MemoryQueryResponse(
        answer=result.answer,
        confidence=result.confidence,
        memories=[QueriedMemory(**memory) for memory in result.memories],
        sources=[EvidenceOut(**source) for source in result.sources],
        trace=result.trace if payload.include_trace else None,
    )


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    payload: MemorySearchRequest,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
) -> MemorySearchResponse:
    """Hybrid retrieval without answer composition: useful for building your own prompt."""
    customer = None
    if payload.customer_id:
        customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{payload.customer_id}' not found.")

    retrieval = await engine.search(
        project=project,
        customer=customer,
        query=payload.query,
        limit=payload.limit,
        types=payload.types,
    )
    return MemorySearchResponse(
        memories=[
            QueriedMemory(
                id=item.memory.id,
                type=str(item.memory.type),
                content=item.memory.content,
                importance=round(float(item.memory.importance), 3),
                confidence=round(float(item.memory.confidence), 3),
                score=round(item.score, 4),
                retrieved_by=sorted(item.strategies),
                source_event_ids=list(item.memory.source_event_ids or []),
            )
            for item in retrieval.memories
        ],
        trace={
            "analysis": retrieval.analysis.as_dict(),
            "strategies": retrieval.strategies_used,
            "ranking": retrieval.explain(),
        },
    )
