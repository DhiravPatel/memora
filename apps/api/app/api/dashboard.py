"""Dashboard read APIs (JWT auth, scoped to a project inside the caller's organization).

These mirror the public API but authenticate as a user, so the dashboard never needs to
hold a project API key in the browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.customers import _sections
from app.core.dependencies import (
    Clearance,
    CurrentUserDep,
    DBSession,
    Engine,
    UserProject,
)
from app.core.queue import enqueue
from app.schemas.agents import SessionOut
from app.schemas.common import DeletionResult, Message, Page
from app.schemas.customers import Customer360, CustomerOut, CustomerTimeline
from app.schemas.events import EventExplanationOut, EventOut, EventPreviewIn
from app.schemas.goals import GoalOut, GoalSummaryOut, GoalUpdate
from app.schemas.health import (
    FeedbackOut,
    FeedbackRequest,
    HealthOut,
    PortfolioHealthOut,
)
from app.schemas.memories import (
    CustomerLinks,
    EntityOut,
    GlossaryEntryIn,
    LearnedTermOut,
    MemoryDetail,
    MemoryGraph,
    MemoryOut,
    VocabularyOut,
)
from app.schemas.query import (
    ContextRequest,
    ContextResponse,
    EvidenceOut,
    MemoryQueryRequest,
    MemoryQueryResponse,
    QueriedMemory,
)
from app.schemas.signals import (
    PortfolioSignalsOut,
    RecommendationsOut,
    SignalReportOut,
)
from app.services.agent_service import AgentService
from app.services.customer360_service import Customer360Service
from app.services.deletion_service import DeletionService
from app.services.event_service import EventService
from app.services.export_service import ExportService
from app.services.feedback_service import FeedbackService
from app.services.goal_service import GoalService
from app.services.health_service import HealthService
from app.services.memory_service import MemoryService
from app.services.serializers import (
    customer_out,
    event_out,
    goal_out,
    recommendation_out,
    session_out,
    signal_report_out,
)
from app.services.signal_service import SignalService
from common.enums import (
    AgentSessionStatus,
    AuditAction,
    EventStatus,
    GoalStatus,
    MemoryStatus,
    MemoryType,
    UserRole,
    VocabularySource,
    VocabularyStatus,
)
from common.errors import NotFoundError
from database.repositories import (
    AuditRepository,
    CustomerRepository,
    EventRepository,
    VocabularyRepository,
)

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["dashboard"])


async def _resolve_customer(session: DBSession, project_id: str, customer_id: str):
    customer = await CustomerRepository(session).resolve(customer_id, project_id)
    if customer is None:
        raise NotFoundError(f"Customer '{customer_id}' not found.")
    return customer


@router.get("/customers", response_model=Page[CustomerOut])
async def list_customers(
    project: UserProject,
    session: DBSession,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CustomerOut]:
    customers, total = await CustomerRepository(session).list(
        project_id=project.id, search=search, limit=limit, offset=offset
    )
    return Page[CustomerOut](
        data=[customer_out(customer) for customer in customers],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/customers/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: str, project: UserProject, session: DBSession
) -> CustomerOut:
    return customer_out(await _resolve_customer(session, project.id, customer_id))


@router.get("/customers/{customer_id}/memories", response_model=Page[MemoryOut])
async def customer_memories(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    type: MemoryType | None = None,
    status: MemoryStatus | None = MemoryStatus.ACTIVE,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[MemoryOut]:
    customer = await _resolve_customer(session, project.id, customer_id)
    memories, total, withheld = await MemoryService(session, cleared=cleared).list_for_customer(
        project=project,
        customer=customer,
        type=type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return Page[MemoryOut](
        data=memories, total=total, limit=limit, offset=offset, withheld=withheld
    )


@router.get("/customers/{customer_id}/timeline", response_model=CustomerTimeline)
async def customer_timeline(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    limit: int = Query(default=100, ge=1, le=500),
) -> CustomerTimeline:
    customer = await _resolve_customer(session, project.id, customer_id)
    return await MemoryService(session, cleared=cleared).timeline(project=project, customer=customer, limit=limit)


@router.get("/customers/{customer_id}/graph", response_model=MemoryGraph)
async def customer_graph(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    depth: int = Query(default=2, ge=1, le=3),
) -> MemoryGraph:
    customer = await _resolve_customer(session, project.id, customer_id)
    return await MemoryService(session, cleared=cleared).graph(project=project, customer=customer, depth=depth)


@router.get("/customers/{customer_id}/export")
async def export_customer(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    current_user: CurrentUserDep,
) -> dict:
    current_user.require(UserRole.MEMBER)
    customer = await _resolve_customer(session, project.id, customer_id)
    return await ExportService(session, cleared=cleared).customer_bundle(
        project=project,
        customer=customer,
        actor_type="user",
        actor_id=current_user.user.id,
    )


@router.delete("/customers/{customer_id}", response_model=DeletionResult)
async def delete_customer(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    current_user: CurrentUserDep,
) -> DeletionResult:
    current_user.require(UserRole.ADMIN)
    counts = await DeletionService(session, cleared=cleared).delete_customer(
        project_id=project.id,
        organization_id=project.organization_id,
        customer_id=customer_id,
        actor_type="user",
        actor_id=current_user.user.id,
    )
    return DeletionResult(resource="customer", resource_id=customer_id, removed=counts.as_dict())


@router.get("/customers/{customer_id}/links", response_model=CustomerLinks)
async def customer_links(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    link_type: str | None = None,
) -> CustomerLinks:
    customer = await _resolve_customer(session, project.id, customer_id)
    return await MemoryService(session, cleared=cleared).links_for_customer(
        project=project, customer=customer, link_type=link_type
    )


@router.get("/customers/{customer_id}/360", response_model=Customer360)
async def customer_360(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    engine: Engine,
    cleared: Clearance,
    include: str | None = Query(default=None),
) -> Customer360:
    """Everything worth knowing about this customer, in one call. See the API-key route."""
    customer = await _resolve_customer(session, project.id, customer_id)
    view = await Customer360Service(session, engine, cleared=cleared).build(
        project=project, customer=customer, include=_sections(include)
    )
    return Customer360(
        customer=view.customer,
        summary=view.summary,
        sections=view.sections,
        withheld=view.withheld,
        generated_at=view.generated_at,
    )


@router.get("/customers/{customer_id}/health", response_model=HealthOut)
async def customer_health(
    customer_id: str, project: UserProject, session: DBSession
) -> HealthOut:
    customer = await _resolve_customer(session, project.id, customer_id)
    result = await HealthService(session).for_customer(project=project, customer=customer)
    return HealthOut(**result.as_dict())


@router.get("/health", response_model=PortfolioHealthOut)
async def portfolio_health(
    project: UserProject,
    session: DBSession,
    bands: str = Query(default="at_risk,critical", description="Comma-separated health bands"),
    limit: int = Query(default=50, ge=1, le=200),
) -> PortfolioHealthOut:
    """Customers whose memory says they need attention, worst first."""
    wanted = tuple(band.strip() for band in bands.split(",") if band.strip())
    results = await HealthService(session).portfolio(project=project, bands=wanted, limit=limit)
    return PortfolioHealthOut(
        project_id=project.id,
        customers=[HealthOut(**result.as_dict()) for result in results],
        bands=list(wanted),
    )


@router.get("/customers/{customer_id}/signals", response_model=SignalReportOut)
async def customer_signals(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    series: bool = Query(default=True),
) -> SignalReportOut:
    customer = await _resolve_customer(session, project.id, customer_id)
    result = await SignalService(session).for_customer(
        project=project, customer=customer, include_series=series
    )
    return signal_report_out(result)


@router.get("/customers/{customer_id}/recommendations", response_model=RecommendationsOut)
async def customer_recommendations(
    customer_id: str, project: UserProject, session: DBSession
) -> RecommendationsOut:
    customer = await _resolve_customer(session, project.id, customer_id)
    actions, report = await SignalService(session).recommendations(
        project=project, customer=customer
    )
    return RecommendationsOut(
        customer_id=customer.id,
        external_id=customer.external_id,
        summary=SignalService.summarise(actions),
        trajectory=str(report.trajectory),
        churn_risk=report.churn_risk,
        recommendations=[recommendation_out(action) for action in actions],
        computed_at=report.computed_at,
    )


@router.get("/signals", response_model=PortfolioSignalsOut)
async def portfolio_signals(
    project: UserProject,
    session: DBSession,
    trajectories: str = Query(
        default="", description="Comma-separated: improving, steady, declining"
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> PortfolioSignalsOut:
    """Every customer's forecast, declining first — the list somebody works through."""
    wanted = tuple(item.strip() for item in trajectories.split(",") if item.strip())
    portfolio = await SignalService(session).portfolio(
        project=project, trajectories=wanted, limit=limit
    )
    return PortfolioSignalsOut(
        project_id=project.id,
        customers=[signal_report_out(result) for result in portfolio.customers],
        trajectories=portfolio.counts,
    )


@router.get("/customers/{customer_id}/goals", response_model=Page[GoalOut])
async def customer_goals(
    customer_id: str,
    project: UserProject,
    session: DBSession,
    status: GoalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[GoalOut]:
    customer = await _resolve_customer(session, project.id, customer_id)
    goals, total = await GoalService(session).list_for_customer(
        project=project, customer=customer, status=status, limit=limit, offset=offset
    )
    return Page[GoalOut](
        data=[goal_out(goal) for goal in goals], total=total, limit=limit, offset=offset
    )


@router.get("/goals", response_model=Page[GoalOut])
async def list_goals(
    project: UserProject,
    session: DBSession,
    status: GoalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[GoalOut]:
    goals, total = await GoalService(session).list_for_project(
        project=project, status=status, limit=limit, offset=offset
    )
    return Page[GoalOut](
        data=[goal_out(goal) for goal in goals], total=total, limit=limit, offset=offset
    )


@router.get("/goals/summary", response_model=GoalSummaryOut)
async def goals_summary(project: UserProject, session: DBSession) -> GoalSummaryOut:
    counts = await GoalService(session).counts(project=project)
    return GoalSummaryOut(**counts.as_dict())


@router.patch("/goals/{goal_id}", response_model=GoalOut)
async def override_goal(
    goal_id: str,
    payload: GoalUpdate,
    project: UserProject,
    session: DBSession,
    user: CurrentUserDep,
) -> GoalOut:
    """A person's verdict on a goal. The tracker leaves overridden goals alone."""
    user.require(UserRole.MEMBER)
    goal = await GoalService(session).override(
        project=project,
        goal_id=goal_id,
        status=GoalStatus(payload.status),
        note=payload.note,
        actor_type="user",
        actor_id=user.user.id,
    )
    return goal_out(goal)


@router.get("/agent/sessions", response_model=Page[SessionOut])
async def list_agent_sessions(
    project: UserProject,
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


@router.get("/agent/sessions/{session_id}", response_model=SessionOut)
async def agent_session_detail(
    session_id: str, project: UserProject, session: DBSession, engine: Engine
) -> SessionOut:
    view = await AgentService(session, engine).detail(project=project, session_id=session_id)
    return session_out(view.session, turns=view.turns, prior=view.prior_sessions)


@router.post("/memories/{memory_id}/feedback", response_model=FeedbackOut)
async def memory_feedback(
    memory_id: str,
    payload: FeedbackRequest,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    current_user: CurrentUserDep,
) -> FeedbackOut:
    current_user.require(UserRole.MEMBER)
    result = await FeedbackService(session, cleared=cleared).submit(
        project=project,
        memory_id=memory_id,
        verdict=payload.verdict,
        content=payload.content,
        note=payload.note,
        actor_type="user",
        actor_id=current_user.user.id,
    )
    return FeedbackOut(**result.as_dict())


@router.get("/events", response_model=Page[EventOut])
async def list_events(
    project: UserProject,
    session: DBSession,
    customer_id: str | None = None,
    event_type: str | None = None,
    status: EventStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[EventOut]:
    resolved = None
    if customer_id:
        resolved = (await _resolve_customer(session, project.id, customer_id)).id
    events, total = await EventRepository(session).list(
        project_id=project.id,
        customer_id=resolved,
        event_type=event_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return Page[EventOut](
        data=[event_out(event) for event in events], total=total, limit=limit, offset=offset
    )


@router.post("/events/preview", response_model=EventExplanationOut)
async def preview_event(
    payload: EventPreviewIn,
    project: UserProject,
    session: DBSession,
    engine: Engine,
) -> EventExplanationOut:
    """What this event would do. Writes nothing.

    Declared before ``/events/{event_id}/retry`` would be reached — not strictly necessary
    while that one is a POST on a different shape, but the ordering is free and the class
    of bug it prevents is a route that silently swallows the literal id "preview".
    """
    customer = await _resolve_customer(session, project.id, payload.customer_id)
    explanation = await engine.preview_event(
        project=project,
        customer=customer,
        event_type=payload.event_type,
        data=payload.data,
        occurred_at=payload.occurred_at,
    )
    return EventExplanationOut(**explanation.as_dict(), duration_ms=explanation.duration_ms)


@router.post("/events/{event_id}/retry", response_model=Message)
async def retry_event(
    event_id: str, project: UserProject, session: DBSession, current_user: CurrentUserDep
) -> Message:
    current_user.require(UserRole.MEMBER)
    result = await EventService(session).retry(project=project, event_id=event_id)
    return Message(message=f"Event {result.event_id} re-queued.")


@router.post("/events/retry-failed", response_model=Message)
async def retry_failed_events(
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> Message:
    """Drain the dead-letter backlog after fixing whatever caused the failures."""
    current_user.require(UserRole.ADMIN)
    requeued = await EventService(session).retry_failed(project=project, limit=limit)
    return Message(message=f"{requeued} failed event(s) re-queued.")


@router.get("/vocabulary", response_model=VocabularyOut)
async def project_vocabulary(
    project: UserProject,
    session: DBSession,
    status: str | None = Query(default="active", description="active | rejected"),
    limit: int = Query(default=100, ge=1, le=400),
    offset: int = Query(default=0, ge=0),
) -> VocabularyOut:
    """Terms this project's own memories say go together.

    Read-only: the table is mined, not configured, so the way to change it is to change
    what customers write — or to rebuild it after a lot of new memory has arrived.
    """
    repository = VocabularyRepository(session)
    wanted = VocabularyStatus(status) if status else None
    terms, total = await repository.list(
        project_id=project.id, status=wanted, limit=limit, offset=offset
    )
    _, curated = await repository.list(
        project_id=project.id, source=VocabularySource.CURATED, limit=1
    )
    _, rejected = await repository.list(
        project_id=project.id, status=VocabularyStatus.REJECTED, limit=1
    )
    return VocabularyOut(
        project_id=project.id,
        terms=[_term_out(item) for item in terms],
        total=total,
        curated=curated,
        rejected=rejected,
        last_mined_at=await repository.last_mined_at(project.id),
    )


def _term_out(item) -> LearnedTermOut:
    return LearnedTermOut(
        term=item.term,
        synonym=item.synonym,
        score=round(float(item.score), 3),
        support=item.support,
        source=str(item.source),
        status=str(item.status),
        note=item.note,
        mined_at=item.mined_at,
    )


@router.post("/vocabulary", response_model=LearnedTermOut, status_code=201)
async def add_glossary_entry(
    payload: GlossaryEntryIn,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
) -> LearnedTermOut:
    """Teach the project a pair directly.

    A curated entry outranks the miner: it is used at full strength, survives every mining
    run, and replaces a mined or rejected row for the same pair.
    """
    current_user.require(UserRole.MEMBER)
    entry = await VocabularyRepository(session).add_curated(
        project_id=project.id,
        term=payload.term,
        synonym=payload.synonym,
        decided_by=current_user.user.id,
        note=payload.note,
    )
    await AuditRepository(session).record(
        action=AuditAction.CONFIGURATION_CHANGE,
        actor_type="user",
        actor_id=current_user.user.id,
        organization_id=project.organization_id,
        project_id=project.id,
        resource_type="vocabulary",
        resource_id=f"{entry.term}~{entry.synonym}",
        metadata={"event": "curated", "note": payload.note},
    )
    return _term_out(entry)


@router.delete("/vocabulary/{term}/{synonym}", response_model=Message)
async def reject_vocabulary_pair(
    term: str,
    synonym: str,
    project: UserProject,
    session: DBSession,
    current_user: CurrentUserDep,
    restore: bool = Query(default=False, description="Undo a rejection instead"),
) -> Message:
    """Throw a pair out — and remember that it was thrown out.

    The rejection is kept rather than the row deleted, because otherwise tonight's mining
    run would learn the same pair again.
    """
    current_user.require(UserRole.MEMBER)
    repository = VocabularyRepository(session)
    if restore:
        await repository.restore(project_id=project.id, term=term, synonym=synonym)
        return Message(message=f"'{term} ~ {synonym}' restored.")

    entry = await repository.reject(
        project_id=project.id, term=term, synonym=synonym, decided_by=current_user.user.id
    )
    if entry is None:
        raise NotFoundError(f"'{term} ~ {synonym}' is not in this project's vocabulary.")
    await AuditRepository(session).record(
        action=AuditAction.CONFIGURATION_CHANGE,
        actor_type="user",
        actor_id=current_user.user.id,
        organization_id=project.organization_id,
        project_id=project.id,
        resource_type="vocabulary",
        resource_id=f"{term}~{synonym}",
        metadata={"event": "rejected"},
    )
    return Message(message=f"'{term} ~ {synonym}' will not be used or relearned.")


@router.post("/vocabulary/rebuild", response_model=Message)
async def rebuild_vocabulary(
    project: UserProject, current_user: CurrentUserDep
) -> Message:
    """Re-mine the vocabulary now instead of waiting for tonight's run."""
    current_user.require(UserRole.MEMBER)
    queued = await enqueue("mine_vocabulary", project.id)
    if queued is None:
        return Message(message="Could not reach the queue; tonight's run will pick it up.")
    return Message(message="Rebuilding this project's vocabulary.")


@router.get("/entities", response_model=Page[EntityOut])
async def list_entities(
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[EntityOut]:
    entities, total = await MemoryService(session, cleared=cleared).entities_for_project(
        project=project, limit=limit, offset=offset
    )
    return Page[EntityOut](data=entities, total=total, limit=limit, offset=offset)


@router.get("/memories", response_model=Page[MemoryOut])
async def list_memories(
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    type: MemoryType | None = None,
    status: MemoryStatus | None = MemoryStatus.ACTIVE,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[MemoryOut]:
    memories, total, withheld = await MemoryService(session, cleared=cleared).list_for_project(
        project=project, type=type, status=status, limit=limit, offset=offset
    )
    return Page[MemoryOut](
        data=memories, total=total, limit=limit, offset=offset, withheld=withheld
    )


@router.get("/memories/{memory_id}", response_model=MemoryDetail)
async def memory_detail(
    memory_id: str, project: UserProject, session: DBSession, cleared: Clearance
) -> MemoryDetail:
    return await MemoryService(session, cleared=cleared).detail(project=project, memory_id=memory_id)


@router.post("/playground/query", response_model=MemoryQueryResponse)
async def playground_query(
    payload: MemoryQueryRequest,
    project: UserProject,
    session: DBSession,
    engine: Engine,
) -> MemoryQueryResponse:
    """The dashboard playground: always returns the retrieval trace."""
    customer = await _resolve_customer(session, project.id, payload.customer_id)
    result = await engine.answer(
        project=project, customer=customer, query=payload.query, limit=payload.limit
    )
    return MemoryQueryResponse(
        answer=result.answer,
        confidence=result.confidence,
        memories=[QueriedMemory(**memory) for memory in result.memories],
        sources=[EvidenceOut(**source) for source in result.sources],
        trace=result.trace,
    )


@router.post("/playground/context", response_model=ContextResponse)
async def playground_context(
    payload: ContextRequest,
    project: UserProject,
    session: DBSession,
    engine: Engine,
) -> ContextResponse:
    customer = await _resolve_customer(session, project.id, payload.customer_id)
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
        prompt_text=context.to_prompt_text(),
        token_count=context.token_count,
        truncated=context.truncated,
    )
