"""Drift (project API key): flags that a standing memory may be out of date (§26 5.5).

A flag is never a change. ``confirm`` writes the change the evidence points to through the
normal memory paths; ``dismiss`` keeps the memory and counts only newer evidence.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import ApiProject, Clearance, DBSession, EmbedderDep, require_scope
from app.schemas.common import Page
from app.schemas.freshness import DriftDecisionIn, DriftOut
from app.services.drift_service import DriftService
from common.enums import ApiKeyScope
from common.errors import NotFoundError
from database.repositories import CustomerRepository

READ = [Depends(require_scope(ApiKeyScope.MEMORY_READ))]
WRITE = [Depends(require_scope(ApiKeyScope.MEMORY_WRITE))]
STATUS = "^(open|confirmed|kept|dismissed|cleared|all)$"
KIND = "^(channel|plan|usage|quiet_problem)$"

router = APIRouter(prefix="/v1/drift", tags=["drift"])


def _actor(request: Request, project_id: str) -> str:
    key = getattr(request.state, "api_key", None)
    return key.id if key is not None else project_id


@router.get("", response_model=Page[DriftOut], dependencies=READ)
async def list_drift(
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    status: str = Query(default="open", pattern=STATUS),
    kind: str | None = Query(default=None, pattern=KIND),
    customer_id: str | None = Query(default=None, max_length=255, description="Your id or the cus_… id."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[DriftOut]:
    """Drift flags, newest first — open ones by default. A flag on a memory this key may not
    read is not shown; ``withheld`` counts them."""
    customer = None
    if customer_id:
        customer = await CustomerRepository(session).resolve(customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")
    rows, total, withheld = await DriftService(session, cleared=cleared).list(
        project=project,
        status=None if status == "all" else status,
        kind=kind,
        customer=customer,
        limit=limit,
        offset=offset,
    )
    return Page[DriftOut](data=rows, total=total, limit=limit, offset=offset, withheld=withheld)


@router.get("/{drift_id}", response_model=DriftOut, dependencies=READ)
async def get_drift(drift_id: str, project: ApiProject, session: DBSession, cleared: Clearance) -> DriftOut:
    return DriftOut(**await DriftService(session, cleared=cleared).get(project=project, drift_id=drift_id))


@router.post("/{drift_id}/confirm", response_model=DriftOut, dependencies=WRITE)
async def confirm_drift(
    drift_id: str,
    payload: DriftDecisionIn,
    request: Request,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    embedder: EmbedderDep,
) -> DriftOut:
    """The evidence is right: write the change — a new preference, plan, habit or a resolved
    problem — superseding what it replaces, with versions and an audit entry."""
    return DriftOut(
        **await DriftService(session, cleared=cleared, embedder=embedder).confirm(
            project=project,
            drift_id=drift_id,
            note=payload.note,
            actor_type="api_key",
            actor_id=_actor(request, project.id),
        )
    )


@router.post("/{drift_id}/keep", response_model=DriftOut, dependencies=WRITE)
async def keep_drift(
    drift_id: str, payload: DriftDecisionIn, request: Request, project: ApiProject, session: DBSession, cleared: Clearance
) -> DriftOut:
    """The memory still holds, and you vouch for it: it is confirmed — new evidence, more
    confidence — and its flags are settled."""
    return DriftOut(
        **await DriftService(session, cleared=cleared).keep(
            project=project,
            drift_id=drift_id,
            note=payload.note,
            actor_type="api_key",
            actor_id=_actor(request, project.id),
        )
    )


@router.post("/{drift_id}/dismiss", response_model=DriftOut, dependencies=WRITE)
async def dismiss_drift(
    drift_id: str, payload: DriftDecisionIn, request: Request, project: ApiProject, session: DBSession, cleared: Clearance
) -> DriftOut:
    """The memory is still right: keep it, and count only evidence newer than now."""
    return DriftOut(
        **await DriftService(session, cleared=cleared).dismiss(
            project=project,
            drift_id=drift_id,
            note=payload.note,
            actor_type="api_key",
            actor_id=_actor(request, project.id),
        )
    )
