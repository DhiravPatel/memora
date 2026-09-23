"""Customer API (project API key): profile, memories, timeline, graph, deletion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    ApiCustomer,
    ApiProject,
    Clearance,
    DBSession,
    Engine,
    require_scope,
)
from app.schemas.admin import (
    CustomerBatchResult,
    CustomerBatchUpsert,
    CustomerMergeRequest,
    CustomerMergeResult,
)
from app.schemas.common import DeletionResult, Page
from app.schemas.customers import (
    Customer360,
    CustomerOut,
    CustomerTimeline,
    CustomerUpsert,
)
from app.schemas.goals import GoalOut
from app.schemas.health import HealthOut
from app.schemas.memories import CustomerLinks, MemoryGraph, MemoryOut
from app.schemas.signals import RecommendationsOut, SignalReportOut
from app.services.customer360_service import SECTIONS, Customer360Service
from app.services.customer_service import CustomerService
from app.services.deletion_service import DeletionService
from app.services.export_service import ExportService
from app.services.goal_service import GoalService
from app.services.health_service import HealthService
from app.services.memory_service import MemoryService
from app.services.serializers import (
    customer_out,
    goal_out,
    recommendation_out,
    signal_report_out,
)
from app.services.signal_service import SignalService
from common.enums import ApiKeyScope, GoalStatus, MemoryStatus, MemoryType
from common.errors import ValidationError
from database.repositories import CustomerRepository

WRITE = [Depends(require_scope(ApiKeyScope.CUSTOMERS_WRITE))]


def _sections(include: str | None) -> frozenset[str] | None:
    """Parse ``?include=health,goals``. Unknown names are refused rather than ignored.

    A typo that silently returns fewer sections is the kind of bug that gets diagnosed as
    "the memory layer forgot my customer".
    """
    if not include:
        return None
    wanted = {name.strip().lower() for name in include.split(",") if name.strip()}
    unknown = wanted - set(SECTIONS)
    if unknown:
        raise ValidationError(
            f"Unknown section(s): {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(SECTIONS)}."
        )
    return frozenset(wanted)


router = APIRouter(prefix="/v1/customers", tags=["customers"])


@router.post("", response_model=CustomerOut, status_code=201, dependencies=WRITE)
async def upsert_customer(
    payload: CustomerUpsert, project: ApiProject, session: DBSession
) -> CustomerOut:
    customer = await CustomerRepository(session).upsert(
        project_id=project.id,
        external_id=payload.external_id,
        email=payload.email,
        name=payload.name,
        metadata=payload.metadata,
    )
    return customer_out(customer)


@router.post("/batch", response_model=CustomerBatchResult, status_code=201, dependencies=WRITE)
async def upsert_customers(
    payload: CustomerBatchUpsert, project: ApiProject, session: DBSession
) -> CustomerBatchResult:
    """Create or update up to 500 customers in one request."""
    result = await CustomerService(session).upsert_many(
        project=project, records=payload.customers
    )
    return CustomerBatchResult(
        created=result.created,
        updated=result.updated,
        customer_ids=[customer.id for customer in result.customers],
    )


@router.get("", response_model=Page[CustomerOut])
async def list_customers(
    project: ApiProject,
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


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer: ApiCustomer) -> CustomerOut:
    return customer_out(customer)


@router.get("/{customer_id}/memories", response_model=Page[MemoryOut])
async def get_customer_memories(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    type: MemoryType | None = None,
    status: MemoryStatus | None = MemoryStatus.ACTIVE,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[MemoryOut]:
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


@router.get("/{customer_id}/timeline", response_model=CustomerTimeline)
async def get_customer_timeline(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    limit: int = Query(default=100, ge=1, le=500),
) -> CustomerTimeline:
    return await MemoryService(session, cleared=cleared).timeline(
        project=project, customer=customer, limit=limit
    )


@router.get("/{customer_id}/graph", response_model=MemoryGraph)
async def get_customer_graph(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    depth: int = Query(default=2, ge=1, le=3),
) -> MemoryGraph:
    return await MemoryService(session, cleared=cleared).graph(project=project, customer=customer, depth=depth)


@router.get("/{customer_id}/links", response_model=CustomerLinks)
async def get_customer_links(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    link_type: str | None = Query(default=None, description="caused_by | resolved_by | relates_to"),
) -> CustomerLinks:
    """How this customer's memories relate: what preceded an outcome, what resolved what."""
    return await MemoryService(session, cleared=cleared).links_for_customer(
        project=project, customer=customer, link_type=link_type
    )


@router.get("/{customer_id}/health", response_model=HealthOut)
async def get_customer_health(
    customer: ApiCustomer, project: ApiProject, session: DBSession
) -> HealthOut:
    """A health score derived from this customer's memories, with the factors behind it."""
    result = await HealthService(session).for_customer(project=project, customer=customer)
    return HealthOut(**result.as_dict())


@router.get("/{customer_id}/signals", response_model=SignalReportOut)
async def get_customer_signals(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    series: bool = Query(default=True, description="Include the stored daily history"),
) -> SignalReportOut:
    """Where this customer is heading, and the observations that say so."""
    result = await SignalService(session).for_customer(
        project=project, customer=customer, include_series=series
    )
    return signal_report_out(result)


@router.get("/{customer_id}/recommendations", response_model=RecommendationsOut)
async def get_customer_recommendations(
    customer: ApiCustomer, project: ApiProject, session: DBSession
) -> RecommendationsOut:
    """What to do about this customer next, most urgent first, with the evidence."""
    service = SignalService(session)
    actions, report = await service.recommendations(project=project, customer=customer)
    return RecommendationsOut(
        customer_id=customer.id,
        external_id=customer.external_id,
        summary=SignalService.summarise(actions),
        trajectory=str(report.trajectory),
        churn_risk=report.churn_risk,
        recommendations=[recommendation_out(action) for action in actions],
        computed_at=report.computed_at,
    )


@router.get("/{customer_id}/goals", response_model=Page[GoalOut])
async def get_customer_goals(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    status: GoalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[GoalOut]:
    """What this customer said they were trying to do, and how far it got."""
    goals, total = await GoalService(session).list_for_customer(
        project=project, customer=customer, status=status, limit=limit, offset=offset
    )
    return Page[GoalOut](
        data=[goal_out(goal) for goal in goals], total=total, limit=limit, offset=offset
    )


@router.get("/{customer_id}/360", response_model=Customer360)
async def get_customer_360(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    engine: Engine,
    cleared: Clearance,
    include: str | None = Query(
        default=None,
        description="Comma-separated sections to build. Omit for all of them.",
    ),
) -> Customer360:
    """Everything worth knowing about this customer, in one call.

    Built for the moment before an agent replies: health, the current subscription, open
    problems, stated preferences and goals, what it relates to, where it is heading, what
    to do about it, and what was said last time.

    Every section comes from the endpoint that already owns it, so the numbers here are the
    numbers there. Sections are individually capped — the consumer is usually a model with
    a token budget — and `?include=` trims further.
    """
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


@router.get("/{customer_id}/export", summary="Export everything known about a customer")
async def export_customer(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    include_versions: bool = Query(default=True),
) -> dict:
    """Events, memories, versions, entities, relationships, links and health, as JSON."""
    return await ExportService(session, cleared=cleared).customer_bundle(
        project=project,
        customer=customer,
        include_versions=include_versions,
        actor_type="api_key",
        actor_id=project.id,
    )


@router.post("/{customer_id}/merge", response_model=CustomerMergeResult, dependencies=WRITE)
async def merge_customer(
    customer_id: str,
    payload: CustomerMergeRequest,
    project: ApiProject,
    session: DBSession,
) -> CustomerMergeResult:
    """Merge this customer into another: events, memories and links all move across."""
    result = await CustomerService(session).merge(
        project=project,
        source_id=customer_id,
        target_id=payload.into,
        actor_type="api_key",
        actor_id=project.id,
    )
    return CustomerMergeResult(**result.as_dict())


@router.delete("/{customer_id}", response_model=DeletionResult, dependencies=WRITE)
async def delete_customer(
    customer_id: str,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
) -> DeletionResult:
    """Forget a customer: events, memories, versions, embeddings and graph links."""
    counts = await DeletionService(session, cleared=cleared).delete_customer(
        project_id=project.id,
        organization_id=project.organization_id,
        customer_id=customer_id,
        actor_type="api_key",
        actor_id=project.id,
    )
    return DeletionResult(
        resource="customer", resource_id=customer_id, removed=counts.as_dict()
    )
