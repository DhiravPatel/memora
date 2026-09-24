"""The project's lifecycle machine and who is in each state (project API key)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.dependencies import ApiProject, DBSession
from app.schemas.common import Page
from app.schemas.customers import CustomerOut
from app.schemas.state import LifecycleOut, TrackTemplateOut
from app.services import state_views
from app.services.serializers import customer_out
from common.errors import ValidationError
from database.repositories import CustomerRepository, CustomerStateRepository

router = APIRouter(prefix="/v1/lifecycle", tags=["lifecycle"])


@router.get("", response_model=LifecycleOut)
async def get_lifecycle(project: ApiProject, session: DBSession) -> LifecycleOut:
    """The machines — the primary lifecycle and every extra track — and how many customers
    are in each state now."""
    return await state_views.lifecycle(session, project=project)


@router.get("/templates", response_model=list[TrackTemplateOut])
async def lifecycle_templates(project: ApiProject) -> list[TrackTemplateOut]:
    """The shipped tracks (engagement, commercial), ready to add to `lifecycle_tracks`."""
    return state_views.templates()


@router.get("/customers", response_model=Page[CustomerOut])
async def customers_in_state(
    project: ApiProject,
    session: DBSession,
    state: str = Query(min_length=1, max_length=64),
    track: str = Query(default="lifecycle", max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CustomerOut]:
    """Every customer currently in a state of a track, most recently arrived first."""
    states_of = state_views.machine_states(project, track)
    if states_of is None:
        raise ValidationError(f"{track!r} is not a lifecycle track of this project.")
    if state not in states_of:
        raise ValidationError(f"{state!r} is not a state of the {track} track.")
    states = CustomerStateRepository(session)
    counts = await states.counts_by_state(project.id, track=track)
    ids = await states.customer_ids_in_state(
        project_id=project.id, state=state, track=track, limit=limit, offset=offset
    )
    customers = await CustomerRepository(session).get_many(ids, project.id) if ids else []
    order = {ident: position for position, ident in enumerate(ids)}
    customers.sort(key=lambda customer: order.get(customer.id, 0))
    return Page[CustomerOut](
        data=[customer_out(customer) for customer in customers],
        total=counts.get(state, 0),
        limit=limit,
        offset=offset,
    )
