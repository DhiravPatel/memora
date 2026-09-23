"""Agent sessions (project API key): memory that survives between conversations.

The three calls an agent needs:

    POST /v1/agent/sessions              → open or resume, and get briefed
    POST /v1/agent/sessions/{id}/turns   → say what happened, get context back
    POST /v1/agent/sessions/{id}/close   → leave a summary for next time

Everything else on this router is for inspecting what an agent did.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import ApiProject, DBSession, Engine, require_scope
from app.schemas.agents import (
    SessionClose,
    SessionCloseOut,
    SessionOpen,
    SessionOut,
    TurnCreate,
    TurnResponse,
)
from app.schemas.common import Page
from app.services.agent_service import AgentService
from app.services.serializers import session_context_out, session_out, turn_out
from common.enums import AgentSessionStatus, ApiKeyScope, TurnRole

# Sessions write memory, so they need the same scope as any other memory write.
WRITE = [Depends(require_scope(ApiKeyScope.MEMORY_WRITE))]

router = APIRouter(prefix="/v1/agent", tags=["agent"])


@router.post("/sessions", response_model=SessionOut, status_code=201, dependencies=WRITE)
async def open_session(
    payload: SessionOpen, project: ApiProject, session: DBSession, engine: Engine
) -> SessionOut:
    """Open (or resume) a session and get everything known about the customer.

    Passing the same ``external_id`` twice resumes rather than duplicating, so this is
    safe to call on every reconnect.
    """
    view = await AgentService(session, engine).open(
        project=project,
        customer_id=payload.customer_id,
        agent=payload.agent,
        external_id=payload.external_id,
        channel=payload.channel,
        token_budget=payload.token_budget,
        metadata=payload.metadata,
        actor_id=project.id,
    )
    return session_out(
        view.session, resumed=view.resumed, context=view.context, prior=view.prior_sessions
    )


@router.post(
    "/sessions/{session_id}/turns", response_model=TurnResponse, status_code=201, dependencies=WRITE
)
async def add_turn(
    session_id: str,
    payload: TurnCreate,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
) -> TurnResponse:
    """Record a turn. Customer turns become memory; agent turns are recorded, not learned."""
    service = AgentService(session, engine)
    result = await service.add_turn(
        project=project,
        session_id=session_id,
        role=TurnRole(payload.role),
        content=payload.content,
        remember=payload.remember,
        retrieve=payload.retrieve,
        occurred_at=payload.occurred_at,
        metadata=payload.metadata,
    )
    return TurnResponse(
        session_id=result.session.id,
        turn=turn_out(result.turn),
        context=session_context_out(result.context) if result.context is not None else None,
        answer=result.answer,
        answer_confidence=result.answer_confidence,
        event_id=result.event_id,
        turn_count=result.session.turn_count,
    )


@router.post("/sessions/{session_id}/close", response_model=SessionCloseOut, dependencies=WRITE)
async def close_session(
    session_id: str,
    payload: SessionClose,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
) -> SessionCloseOut:
    """Close the session and write what it established into long-term memory."""
    closed, summary, memory_id = await AgentService(session, engine).close(
        project=project,
        session_id=session_id,
        write_summary=payload.write_summary,
        outcome=payload.outcome,
        actor_id=project.id,
    )
    return SessionCloseOut(
        session=session_out(closed), summary=summary, summary_memory_id=memory_id
    )


@router.get("/sessions", response_model=Page[SessionOut])
async def list_sessions(
    project: ApiProject,
    session: DBSession,
    engine: Engine,
    customer_id: str | None = None,
    status: AgentSessionStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[SessionOut]:
    sessions, total = await AgentService(session, engine).list(
        project=project, customer_id=customer_id, status=status, limit=limit, offset=offset
    )
    return Page[SessionOut](
        data=[session_out(item) for item in sessions], total=total, limit=limit, offset=offset
    )


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str, project: ApiProject, session: DBSession, engine: Engine
) -> SessionOut:
    """The session with its full transcript."""
    view = await AgentService(session, engine).detail(project=project, session_id=session_id)
    return session_out(view.session, turns=view.turns, prior=view.prior_sessions)
