"""Customer API (project API key): profile, memories, timeline, graph, deletion."""

from __future__ import annotations

from datetime import datetime

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
from app.schemas.changes import ChangesOut, CompareOut
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
from app.schemas.state import (
    CurrentStateOut,
    CustomerFactsOut,
    CustomerStateOut,
    SnapshotOut,
    SnapshotSummaryOut,
    StateRefreshOut,
    StateSetIn,
)
from app.services import state_views
from app.services.changes_service import ChangesService
from app.services.customer360_service import SECTIONS, Customer360Service
from app.services.customer_service import CustomerService
from app.services.customer_state_service import CustomerStateService
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
from common.time import ensure_utc
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
    cleared: Clearance,
    series: bool = Query(default=True, description="Include the stored daily history"),
) -> SignalReportOut:
    """Where this customer is heading, and the observations that say so."""
    result = await SignalService(session, cleared=cleared).for_customer(
        project=project, customer=customer, include_series=series
    )
    return signal_report_out(result)


@router.get("/{customer_id}/recommendations", response_model=RecommendationsOut)
async def get_customer_recommendations(
    customer: ApiCustomer, project: ApiProject, session: DBSession, cleared: Clearance
) -> RecommendationsOut:
    """What to do about this customer next, most urgent first, with the evidence.

    Judged over everything the customer said; words quoted from a memory this key may not
    read are shown as ``[withheld]``.
    """
    service = SignalService(session, cleared=cleared)
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
    cleared: Clearance,
    status: GoalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[GoalOut]:
    """What this customer said they were trying to do, and how far it got."""
    goals, total = await GoalService(session, cleared=cleared).list_for_customer(
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


# ------------------------------------------------------ facts, lifecycle, snapshots

MEMORY_READ = [Depends(require_scope(ApiKeyScope.MEMORY_READ))]


@router.get("/{customer_id}/facts", response_model=CustomerFactsOut, dependencies=MEMORY_READ)
async def get_customer_facts(
    customer: ApiCustomer, project: ApiProject, session: DBSession, cleared: Clearance
) -> CustomerFactsOut:
    """Every fact a rule can read about this customer, with the evidence behind each.

    What guardrails, the lifecycle, workflows and flags decide on. Shown redacted to a
    caller without clearance: counts stay whole, values that came only from restricted
    memories are removed and listed in `withheld_facts`.
    """
    return await state_views.customer_facts(session, project=project, customer=customer, cleared=cleared)


@router.get("/{customer_id}/state", response_model=CurrentStateOut)
async def get_customer_state(
    customer: ApiCustomer, project: ApiProject, session: DBSession, cleared: Clearance
) -> CurrentStateOut:
    """Where this customer is in the lifecycle, and the transition that put them there."""
    return await state_views.current_state(session, project=project, customer=customer, cleared=cleared)


@router.get("/{customer_id}/state/history", response_model=Page[CustomerStateOut])
async def get_customer_state_history(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    track: str = Query(default="lifecycle", max_length=40, description='A track name, or "all"'),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CustomerStateOut]:
    """Every state the customer has been in on a track, newest first, each with its reasons."""
    return await state_views.state_history(
        session, project=project, customer=customer, cleared=cleared, limit=limit, offset=offset, track=track
    )


@router.put("/{customer_id}/state", response_model=CustomerStateOut, dependencies=WRITE)
async def set_customer_state(
    payload: StateSetIn, customer: ApiCustomer, project: ApiProject, session: DBSession
) -> CustomerStateOut:
    """Set the state on a track by hand. Pinned by default: the machine leaves it alone
    until released."""
    row = await CustomerStateService(session).set_state(
        project=project,
        customer=customer,
        state=payload.state,
        pin=payload.pin,
        pin_days=payload.pin_days,
        note=payload.note,
        track=payload.track,
        actor_type="api_key",
        actor_id=project.id,
    )
    return state_views.state_out(row, sanitize=False)


@router.delete("/{customer_id}/state/pin", response_model=CustomerStateOut, dependencies=WRITE)
async def release_customer_state(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    track: str = Query(default="lifecycle", max_length=40),
) -> CustomerStateOut:
    """Hand the customer back to the machine on a track."""
    row = await CustomerStateService(session).release(
        project=project, customer=customer, actor_type="api_key", actor_id=project.id, track=track
    )
    return state_views.state_out(row, sanitize=False)


@router.post("/{customer_id}/state/refresh", response_model=StateRefreshOut, dependencies=WRITE)
async def refresh_customer_state(
    customer: ApiCustomer, project: ApiProject, session: DBSession
) -> StateRefreshOut:
    """Re-evaluate the lifecycle now instead of waiting for the next event or night."""
    return await state_views.refresh_state(session, project=project, customer=customer)


@router.get("/{customer_id}/snapshots", response_model=Page[SnapshotSummaryOut], dependencies=MEMORY_READ)
async def list_customer_snapshots(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[SnapshotSummaryOut]:
    """Every material change in what we knew about this customer, newest first."""
    return await state_views.snapshots(
        session,
        project=project,
        customer=customer,
        cleared=cleared,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/{customer_id}/snapshots/at", response_model=SnapshotOut, dependencies=MEMORY_READ)
async def get_customer_snapshot_at(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    time: datetime = Query(description="ISO 8601. The newest snapshot at or before it is returned."),
) -> SnapshotOut:
    """What we knew about this customer at a moment in the past."""
    return await state_views.snapshot_at(
        session, project=project, customer=customer, moment=ensure_utc(time), cleared=cleared
    )


@router.get("/{customer_id}/snapshots/{snapshot_id}", response_model=SnapshotOut, dependencies=MEMORY_READ)
async def get_customer_snapshot(
    snapshot_id: str,
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
) -> SnapshotOut:
    return await state_views.snapshot_detail(
        session, project=project, customer=customer, snapshot_id=snapshot_id, cleared=cleared
    )


# ------------------------------------------------------------- what changed


def _types(types: str | None) -> set[str] | None:
    return {name.strip().lower() for name in types.split(",") if name.strip()} if types else None


@router.get("/{customer_id}/changes", response_model=ChangesOut, dependencies=MEMORY_READ)
async def get_customer_changes(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    since: str | None = Query(
        default=None,
        max_length=64,
        description="A span (7d, 12h, 2w, 3mo), an ISO time, a snapshot id, `last_session` or `last_run`. Default 30d.",
    ),
    until: str | None = Query(default=None, max_length=64, description="An ISO time, a span back from now, or a snapshot id. Default now."),
    agent: str | None = Query(default=None, max_length=120, description="With last_session/last_run: only this agent's."),
    types: str | None = Query(default=None, max_length=300, description="Comma-separated change types to keep."),
    order: str = Query(default="time", pattern="^(time|importance)$"),
    limit: int = Query(default=50, ge=1, le=200),
) -> ChangesOut:
    """What changed about this customer in a window — problems opened and resolved, plan and
    preference changes with before and after, lifecycle moves with reasons, health crossing
    a band, goals, intents, signals and activity — and what they looked like then and now."""
    service = ChangesService(session, cleared=cleared)
    window = await service.window(project=project, customer=customer, since=since, until=until, agent=agent)
    return await service.changes(
        project=project, customer=customer, window=window, types=_types(types), order=order, limit=limit
    )


@router.get("/{customer_id}/compare", response_model=CompareOut, dependencies=MEMORY_READ)
async def compare_customer(
    customer: ApiCustomer,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    start: str = Query(alias="from", max_length=64, description="An ISO time, a span back from now, or a snapshot id."),
    end: str | None = Query(default=None, alias="to", max_length=64, description="Default now."),
) -> CompareOut:
    """The customer then and now, side by side, with every fact that differs."""
    return await ChangesService(session, cleared=cleared).compare(
        project=project, customer=customer, start=start, end=end
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
