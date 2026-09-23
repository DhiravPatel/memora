"""Memory API (project API key): inspection, manual authoring and deletion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    ApiProject,
    Clearance,
    DBSession,
    Engine,
    require_scope,
)
from app.schemas.common import DeletionResult, Page
from app.schemas.health import FeedbackOut, FeedbackRequest
from app.schemas.memories import EntityOut, MemoryCreate, MemoryDetail, MemoryOut
from app.services.deletion_service import DeletionService
from app.services.feedback_service import FeedbackService
from app.services.memory_service import MemoryService
from common.enums import ApiKeyScope, MemoryStatus, MemoryType
from common.errors import NotFoundError
from database.repositories import CustomerRepository

WRITE = [Depends(require_scope(ApiKeyScope.MEMORY_WRITE))]

router = APIRouter(prefix="/v1/memories", tags=["memories"])


@router.get("", response_model=Page[MemoryOut])
async def list_memories(
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    customer_id: str | None = None,
    type: MemoryType | None = None,
    status: MemoryStatus | None = MemoryStatus.ACTIVE,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[MemoryOut]:
    resolved_customer = None
    if customer_id:
        resolved_customer = await CustomerRepository(session).resolve(customer_id, project.id)
        if resolved_customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")

    memories, total, withheld = await MemoryService(session, cleared=cleared).list_for_project(
        project=project,
        customer=resolved_customer,
        type=type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return Page[MemoryOut](
        data=memories, total=total, limit=limit, offset=offset, withheld=withheld
    )


@router.post("", response_model=MemoryOut, status_code=201, dependencies=WRITE)
async def create_memory(
    payload: MemoryCreate,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
    cleared: Clearance,
) -> MemoryOut:
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")
    memory = await MemoryService(session, cleared=cleared).create_manual(
        project=project,
        customer=customer,
        type=payload.type,
        content=payload.content,
        importance=payload.importance,
        confidence=payload.confidence,
    )
    # A memory written by hand has to move goals too, otherwise a goal stated through this
    # endpoint would sit untracked until some unrelated event happened to arrive.
    await engine.refresh_goals(project=project, customer=customer)
    return memory


@router.get("/entities", response_model=Page[EntityOut])
async def list_entities(
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[EntityOut]:
    entities, total = await MemoryService(session, cleared=cleared).entities_for_project(
        project=project, limit=limit, offset=offset
    )
    return Page[EntityOut](data=entities, total=total, limit=limit, offset=offset)


@router.get("/{memory_id}", response_model=MemoryDetail)
async def get_memory(
    memory_id: str, project: ApiProject, session: DBSession, cleared: Clearance
) -> MemoryDetail:
    """A memory with its full version history: how the belief evolved, and why."""
    return await MemoryService(session, cleared=cleared).detail(project=project, memory_id=memory_id)


@router.post("/{memory_id}/feedback", response_model=FeedbackOut, dependencies=WRITE)
async def submit_feedback(
    memory_id: str,
    payload: FeedbackRequest,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
) -> FeedbackOut:
    """Confirm, reject or correct a memory. Confidence moves; history is preserved."""
    result = await FeedbackService(session, cleared=cleared).submit(
        project=project,
        memory_id=memory_id,
        verdict=payload.verdict,
        content=payload.content,
        note=payload.note,
        actor_type="api_key",
        actor_id=project.id,
    )
    return FeedbackOut(**result.as_dict())


@router.delete("/{memory_id}", response_model=DeletionResult, dependencies=WRITE)
async def delete_memory(
    memory_id: str, project: ApiProject, session: DBSession, cleared: Clearance
) -> DeletionResult:
    await DeletionService(session, cleared=cleared).delete_memory(
        project_id=project.id,
        organization_id=project.organization_id,
        memory_id=memory_id,
        actor_type="api_key",
        actor_id=project.id,
    )
    return DeletionResult(resource="memory", resource_id=memory_id)
