"""Agents in the dashboard (JWT): profiles, the approvals queue, checks, runs, simulation.

Reads are open to every member of the organization; editing profiles needs ``admin``;
deciding an approval needs ``member`` — it is a judgement about one customer, the kind of
call support leads make all day, not a change to how the project is configured.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from app.core.dependencies import Clearance, CurrentUserDep, DBSession, UserProject
from app.schemas.agent_policy import (
    ActionOut,
    AgentActivityOut,
    AgentCheckOut,
    AgentProfileIn,
    AgentProfileOut,
    AgentProfileUpdate,
    ApprovalDecisionIn,
    ApprovalOut,
    DashboardCheckIn,
    GuardrailCatalogOut,
    RunExplanationOut,
    RunOut,
    RunSummaryOut,
    RunTraceOut,
)
from app.schemas.common import Message, Page
from app.services.agent_profile_service import AgentProfileService
from app.services.guardrail_service import GuardrailService
from app.services.run_service import RunService
from common.enums import UserRole
from common.errors import NotFoundError
from common.time import ensure_utc, utcnow
from database.access import access_for_types, access_scope
from database.repositories import (
    AgentApprovalRepository,
    AgentCheckRepository,
    AgentProfileRepository,
    CustomerRepository,
    QueryLogRepository,
)
from memory_engine.guardrails import catalog as guardrail_catalog_data

router = APIRouter(prefix="/v1/projects/{project_id}/agent", tags=["dashboard", "agents"])


async def _customer(session, project_id: str, customer_id: str | None):
    if not customer_id:
        return None
    customer = await CustomerRepository(session).resolve(customer_id, project_id)
    if customer is None:
        raise NotFoundError(f"Customer '{customer_id}' not found.")
    return customer


# -------------------------------------------------------------------- profiles


@router.get("/profiles", response_model=list[AgentProfileOut])
async def list_profiles(project: UserProject, session: DBSession) -> list[AgentProfileOut]:
    return await AgentProfileService(session).list(project)


@router.post("/profiles", response_model=AgentProfileOut, status_code=201)
async def create_profile(
    payload: AgentProfileIn, project: UserProject, session: DBSession, user: CurrentUserDep
) -> AgentProfileOut:
    user.require(UserRole.ADMIN)
    return await AgentProfileService(session).create(
        project=project, payload=payload, actor_type="user", actor_id=user.user.id
    )


@router.patch("/profiles/{profile_id}", response_model=AgentProfileOut)
async def update_profile(
    profile_id: str,
    payload: AgentProfileUpdate,
    project: UserProject,
    session: DBSession,
    user: CurrentUserDep,
) -> AgentProfileOut:
    user.require(UserRole.ADMIN)
    return await AgentProfileService(session).update(
        project=project, profile_id=profile_id, payload=payload, actor_type="user", actor_id=user.user.id
    )


@router.delete("/profiles/{profile_id}", response_model=Message)
async def delete_profile(
    profile_id: str, project: UserProject, session: DBSession, user: CurrentUserDep
) -> Message:
    user.require(UserRole.ADMIN)
    await AgentProfileService(session).delete(
        project=project, profile_id=profile_id, actor_type="user", actor_id=user.user.id
    )
    return Message(message="Agent profile deleted.")


# ------------------------------------------------------------------ guardrails


@router.get("/guardrails", response_model=GuardrailCatalogOut)
async def guardrail_catalog(project: UserProject) -> GuardrailCatalogOut:
    return GuardrailCatalogOut(**guardrail_catalog_data())


@router.post("/simulate", response_model=AgentCheckOut)
async def simulate_check(
    payload: DashboardCheckIn, project: UserProject, session: DBSession, cleared: Clearance
) -> AgentCheckOut:
    """What would happen if an agent — optionally acting as a given profile — asked this?

    Never recorded, never files an approval: this is for writing and testing policy.
    """
    customer = await _customer(session, project.id, payload.customer_id)
    profile = None
    if payload.profile_id:
        profile = await AgentProfileRepository(session).get(payload.profile_id, project.id)
        if profile is None:
            raise NotFoundError(f"Agent profile '{payload.profile_id}' not found.")
    # Shown as the profile would see it, so a simulation cannot be used to read around it.
    access = access_for_types(profile.readable_types if profile else None, profile=profile.name if profile else None)
    with access_scope(access):
        service = GuardrailService(
            session, cleared=cleared and (profile is None or bool(profile.can_read_restricted))
        )
        outcome = await service.check(
            project=project,
            customer=customer,
            action=payload.action,
            request=payload.request,
            profile=profile,
            agent=payload.agent,
            dry_run=True,
        )
        return await service.outcome_out(project, outcome)


@router.get("/checks", response_model=Page[AgentCheckOut])
async def list_checks(
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    customer_id: str | None = None,
    decision: str | None = Query(default=None, pattern="^(allow|require_approval|deny)$"),
    agent: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AgentCheckOut]:
    customer = await _customer(session, project.id, customer_id)
    rows, total = await GuardrailService(session, cleared=cleared).list_checks(
        project=project,
        customer=customer,
        decision=decision,
        agent=agent,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return Page[AgentCheckOut](data=rows, total=total, limit=limit, offset=offset)


@router.get("/checks/{check_id}", response_model=AgentCheckOut)
async def get_check(check_id: str, project: UserProject, session: DBSession, cleared: Clearance) -> AgentCheckOut:
    return await GuardrailService(session, cleared=cleared).get_check(project=project, check_id=check_id)


# ------------------------------------------------------------------- approvals


@router.get("/approvals", response_model=Page[ApprovalOut])
async def list_approvals(
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    status: str | None = Query(default=None, pattern="^(pending|approved|rejected|expired|used)$"),
    customer_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ApprovalOut]:
    customer = await _customer(session, project.id, customer_id)
    rows, total = await GuardrailService(session, cleared=cleared).list_approvals(
        project=project, status=status, customer=customer, limit=limit, offset=offset
    )
    return Page[ApprovalOut](data=rows, total=total, limit=limit, offset=offset)


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: str, project: UserProject, session: DBSession, cleared: Clearance
) -> ApprovalOut:
    return await GuardrailService(session, cleared=cleared).get_approval(project=project, approval_id=approval_id)


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionIn,
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    user: CurrentUserDep,
) -> ApprovalOut:
    user.require(UserRole.MEMBER)
    service = GuardrailService(session, cleared=cleared)
    approval = await service.decide(
        project=project,
        approval_id=approval_id,
        approve=payload.decision == "approve",
        note=payload.note,
        actor_type="user",
        actor_id=user.user.id,
    )
    return await service.approval_out(project, approval)


# ------------------------------------------------------------------------ runs


@router.get("/runs", response_model=Page[RunSummaryOut])
async def list_runs(
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    customer_id: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    kind: str | None = Query(default=None, pattern="^(query|context)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[RunSummaryOut]:
    customer = await _customer(session, project.id, customer_id)
    rows, total = await RunService(session, cleared=cleared).list(
        project=project,
        customer=customer,
        agent=agent,
        session_id=session_id,
        kind=kind,
        since=ensure_utc(since) if since else None,
        until=ensure_utc(until) if until else None,
        limit=limit,
        offset=offset,
    )
    return Page[RunSummaryOut](data=rows, total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, project: UserProject, session: DBSession, cleared: Clearance) -> RunOut:
    return await RunService(session, cleared=cleared).get(project=project, run_id=run_id)


@router.get("/actions", response_model=Page[ActionOut])
async def dashboard_actions(
    project: UserProject,
    session: DBSession,
    cleared: Clearance,
    customer_id: str | None = None,
    action: str | None = Query(default=None, max_length=80),
    status: str | None = Query(
        default=None, pattern="^(allowed|pending_approval|denied|done|failed|cancelled|expired)$"
    ),
    agent: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ActionOut]:
    customer = None
    if customer_id:
        customer = await CustomerRepository(session).resolve(customer_id, project.id)
        if customer is None:
            raise NotFoundError(f"Customer '{customer_id}' not found.")
    rows, total = await GuardrailService(session, cleared=cleared).list_actions(
        project=project, customer=customer, action=action, status=status, agent=agent, limit=limit, offset=offset
    )
    return Page[ActionOut](data=rows, total=total, limit=limit, offset=offset)


@router.get("/actions/{action_id}", response_model=ActionOut)
async def dashboard_action(action_id: str, project: UserProject, session: DBSession, cleared: Clearance) -> ActionOut:
    return await GuardrailService(session, cleared=cleared).get_action(project=project, action_id=action_id)


@router.get("/runs/{run_id}/explain", response_model=RunExplanationOut)
async def explain_run(
    run_id: str, project: UserProject, session: DBSession, cleared: Clearance
) -> RunExplanationOut:
    return await RunService(session, cleared=cleared).explain(project=project, run_id=run_id)


@router.get("/runs/{run_id}/trace", response_model=RunTraceOut)
async def trace_run(run_id: str, project: UserProject, session: DBSession, cleared: Clearance) -> RunTraceOut:
    return await RunService(session, cleared=cleared).trace(project=project, run_id=run_id)


# -------------------------------------------------------------------- activity


@router.get("/activity", response_model=AgentActivityOut)
async def agent_activity(
    project: UserProject, session: DBSession, days: int = Query(default=7, ge=1, le=90)
) -> AgentActivityOut:
    """The header of the Agents page: what agents asked, what they were told, what waits."""
    since = utcnow() - timedelta(days=days)
    checks = AgentCheckRepository(session)
    return AgentActivityOut(
        window_days=days,
        checks=await checks.counts_by_decision(project.id, since=since),
        approvals=await AgentApprovalRepository(session).counts_by_status(project.id),
        top_rules=[{"rule": rule, "count": count} for rule, count in await checks.top_rules(project.id, since=since)],
        runs=await QueryLogRepository(session).count_since(project_id=project.id, since=since),
    )
