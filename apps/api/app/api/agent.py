"""Agents (project API key): sessions, guardrails, approvals, runs and profiles.

The three calls an agent needs for memory that survives between conversations:

    POST /v1/agent/sessions              → open or resume, and get briefed
    POST /v1/agent/sessions/{id}/turns   → say what happened, get context back
    POST /v1/agent/sessions/{id}/close   → leave a summary for next time

The one call it needs before it acts (§26 3.2):

    POST /v1/agent/check                 → allow, require_approval or deny, and why

Everything else on this router is for inspecting what an agent did (§26 3.4) and for
managing the profiles agents act as (§26 3.1).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import ApiProject, Clearance, DBSession, Engine, require_scope
from app.schemas.agent_policy import (
    ActionCompleteIn,
    ActionOut,
    ActionRequestIn,
    AgentCheckIn,
    AgentCheckOut,
    AgentProfileIn,
    AgentProfileOut,
    AgentProfileUpdate,
    ApprovalDecisionIn,
    ApprovalOut,
    GuardrailCatalogOut,
    RunExplanationOut,
    RunOut,
    RunSummaryOut,
    RunTraceOut,
)
from app.schemas.agents import (
    SessionClose,
    SessionCloseOut,
    SessionOpen,
    SessionOut,
    TurnCreate,
    TurnResponse,
)
from app.schemas.common import Message, Page
from app.services.agent_profile_service import AgentProfileService
from app.services.agent_service import AgentService
from app.services.guardrail_service import GuardrailService
from app.services.reader import Reader
from app.services.run_service import RunService, session_for
from app.services.serializers import session_context_out, session_out, turn_out
from common.enums import AgentSessionStatus, ApiKeyScope, TurnRole
from common.errors import AuthorizationError, NotFoundError, ValidationError
from common.time import ensure_utc
from database.access import current_access
from database.repositories import CustomerRepository
from memory_engine.guardrails import catalog as guardrail_catalog_data

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
    mask = await Reader(session, cleared=engine.cleared).session_mask(project.id, view.prior_sessions)
    return session_out(
        view.session, resumed=view.resumed, context=view.context, prior=view.prior_sessions, mask=mask
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
    mask = await Reader(session, cleared=engine.cleared).session_mask(project.id, sessions)
    return Page[SessionOut](
        data=[session_out(item, mask=mask) for item in sessions], total=total, limit=limit, offset=offset
    )


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str, project: ApiProject, session: DBSession, engine: Engine
) -> SessionOut:
    """The session with its full transcript."""
    view = await AgentService(session, engine).detail(project=project, session_id=session_id)
    mask = await Reader(session, cleared=engine.cleared).session_mask(
        project.id, [view.session], view.turns
    )
    return session_out(view.session, turns=view.turns, prior=view.prior_sessions, mask=mask)


# ------------------------------------------------------------------ guardrails


@router.post("/check", response_model=AgentCheckOut)
async def check_action(
    payload: AgentCheckIn,
    request: Request,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
) -> AgentCheckOut:
    """Ask before acting: may this agent do this, to this customer, now?

    Returns ``allow``, ``require_approval`` or ``deny`` with every reason and its evidence.
    A ``require_approval`` files a request for a person and returns it; once approved,
    check again with ``approval_id`` to redeem it — exactly once, for exactly this request.
    Every check is recorded unless ``dry_run`` is set.
    """
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")
    session_id = await _session_id(session, project.id, customer.id, payload.session_id)
    service = GuardrailService(session, cleared=cleared)
    key = getattr(request.state, "api_key", None)
    outcome = await service.check(
        project=project,
        customer=customer,
        action=payload.action,
        request=payload.request,
        profile=getattr(request.state, "agent_profile", None),
        agent=payload.agent or current_access().agent,
        api_key_id=key.id if key is not None else None,
        approval_id=payload.approval_id,
        session_id=session_id,
        dry_run=payload.dry_run,
    )
    return await service.outcome_out(project, outcome)


# ------------------------------------------------------------- action gateway


@router.post("/actions/request", response_model=ActionOut, status_code=201)
async def request_action(
    payload: ActionRequestIn,
    request: Request,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
) -> ActionOut:
    """The one call an agent makes before it acts (§26 4.5).

    Decides like ``/check`` — the profile, the built-in rules (opt-outs included), the
    project's rules and its automatic approval limits — and records the action. ``status``
    says what to do: ``allowed`` (go ahead, then report back with ``/complete``),
    ``pending_approval`` (a person was asked; call ``/proceed`` once they decide) or
    ``denied``. Sending the same ``idempotency_key`` again returns the same action.
    """
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")
    if payload.dry_run:
        raise ValidationError("An action request is always recorded; use /v1/agent/check with dry_run to simulate.")
    session_id = await _session_id(session, project.id, customer.id, payload.session_id)
    service = GuardrailService(session, cleared=cleared)
    key = getattr(request.state, "api_key", None)
    record = await service.request_action(
        project=project,
        customer=customer,
        action=payload.action,
        request=payload.request,
        profile=getattr(request.state, "agent_profile", None),
        agent=payload.agent or current_access().agent,
        api_key_id=key.id if key is not None else None,
        approval_id=payload.approval_id,
        session_id=session_id,
        idempotency_key=payload.idempotency_key,
    )
    return await service.action_out(project, record)


@router.get("/actions", response_model=Page[ActionOut])
async def list_actions(
    project: ApiProject,
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
    """Actions agents requested through the gateway, newest first — a customer's action history."""
    customer = await _customer(session, project.id, customer_id)
    rows, total = await GuardrailService(session, cleared=cleared).list_actions(
        project=project, customer=customer, action=action, status=status, agent=agent, limit=limit, offset=offset
    )
    return Page[ActionOut](data=rows, total=total, limit=limit, offset=offset)


@router.get("/actions/{action_id}", response_model=ActionOut)
async def get_action(action_id: str, project: ApiProject, session: DBSession, cleared: Clearance) -> ActionOut:
    """Where an action stands, and ``next_step``: what the agent should do now."""
    return await GuardrailService(session, cleared=cleared).get_action(project=project, action_id=action_id)


@router.post("/actions/{action_id}/proceed", response_model=ActionOut)
async def proceed_action(
    action_id: str, request: Request, project: ApiProject, session: DBSession, cleared: Clearance
) -> ActionOut:
    """Go ahead with an action a person approved: the rules run again on the facts as they
    are now, and the approval is redeemed. Still waiting, rejected or lapsed says so."""
    service = GuardrailService(session, cleared=cleared)
    key = getattr(request.state, "api_key", None)
    record = await service.proceed_action(
        project=project,
        action_id=action_id,
        profile=getattr(request.state, "agent_profile", None),
        api_key_id=key.id if key is not None else None,
    )
    return await service.action_out(project, record)


@router.post("/actions/{action_id}/complete", response_model=ActionOut)
async def complete_action(
    action_id: str, payload: ActionCompleteIn, project: ApiProject, session: DBSession, cleared: Clearance
) -> ActionOut:
    """Report what happened — ``done``, ``failed`` or ``cancelled`` — so the customer's action
    history (``actions.*`` facts) is what really happened."""
    service = GuardrailService(session, cleared=cleared)
    record = await service.complete_action(
        project=project,
        action_id=action_id,
        outcome=payload.outcome,
        note=payload.note,
        external_ref=payload.external_ref,
    )
    return await service.action_out(project, record)


@router.get("/checks", response_model=Page[AgentCheckOut])
async def list_checks(
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    customer_id: str | None = None,
    decision: str | None = Query(default=None, pattern="^(allow|require_approval|deny)$"),
    agent: str | None = None,
    session_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AgentCheckOut]:
    """Every recorded check, newest first — what agents asked to do and what they were told."""
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
async def get_check(check_id: str, project: ApiProject, session: DBSession, cleared: Clearance) -> AgentCheckOut:
    return await GuardrailService(session, cleared=cleared).get_check(project=project, check_id=check_id)


@router.get("/guardrails", response_model=GuardrailCatalogOut)
async def guardrail_catalog(project: ApiProject) -> GuardrailCatalogOut:
    """The actions the built-in rules recognise, and the built-in rules themselves."""
    return GuardrailCatalogOut(**guardrail_catalog_data())


# ------------------------------------------------------------------- approvals


@router.get("/approvals", response_model=Page[ApprovalOut])
async def list_approvals(
    project: ApiProject,
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
    approval_id: str, project: ApiProject, session: DBSession, cleared: Clearance
) -> ApprovalOut:
    """Poll this while waiting — or subscribe to ``agent.approval_decided``."""
    return await GuardrailService(session, cleared=cleared).get_approval(project=project, approval_id=approval_id)


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalOut,
    dependencies=[Depends(require_scope(ApiKeyScope.APPROVALS_DECIDE))],
)
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionIn,
    request: Request,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
) -> ApprovalOut:
    """Approve or reject — for an approvals bot in Slack, a ticketing system, a phone.

    Needs ``approvals:decide``, which ``admin`` does not confer; a key bound to an agent
    profile can never decide, and no key can decide a request it made itself.
    """
    key = getattr(request.state, "api_key", None)
    if key is None:
        # The legacy project key is full-scope for compatibility, but deciding is a grant
        # nobody could have meant to give it.
        raise AuthorizationError("Create a key with the approvals:decide scope to decide approvals.")
    if getattr(request.state, "agent_profile", None) is not None:
        raise AuthorizationError("A key that acts as an agent cannot decide approvals.")
    service = GuardrailService(session, cleared=cleared)
    approval = await service.decide(
        project=project,
        approval_id=approval_id,
        approve=payload.decision == "approve",
        note=payload.note,
        actor_type="api_key",
        actor_id=f"api_key:{key.id}",
        api_key_id=key.id,
    )
    return await service.approval_out(project, approval)


# ------------------------------------------------------------------------ runs


@router.get("/runs", response_model=Page[RunSummaryOut])
async def list_runs(
    project: ApiProject,
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
    """Every answer and briefing an agent asked for, newest first."""
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
async def get_run(run_id: str, project: ApiProject, session: DBSession, cleared: Clearance) -> RunOut:
    return await RunService(session, cleared=cleared).get(project=project, run_id=run_id)


@router.get("/runs/{run_id}/explain", response_model=RunExplanationOut)
async def explain_run(
    run_id: str, project: ApiProject, session: DBSession, cleared: Clearance
) -> RunExplanationOut:
    """Why did the agent say that? The memories it used — as they were then and as they
    are now — why each ranked where it did, what was held back, the customer's recorded
    state at that moment, and the guardrail checks around it."""
    return await RunService(session, cleared=cleared).explain(project=project, run_id=run_id)


@router.get("/runs/{run_id}/trace", response_model=RunTraceOut)
async def trace_run(run_id: str, project: ApiProject, session: DBSession, cleared: Clearance) -> RunTraceOut:
    """Why did my agent do this? What it was given (cited, handed over, or retrieved and not
    used), what it was **not** given and why — ranked below the cut, capped by type,
    dropped by the token budget, superseded by a newer memory, expired, restricted, outside
    its profile — the decision it came to with its confidence, and the guardrail checks
    around it."""
    return await RunService(session, cleared=cleared).trace(project=project, run_id=run_id)


# -------------------------------------------------------------------- profiles

ADMIN = [Depends(require_scope(ApiKeyScope.ADMIN))]


@router.get("/profiles", response_model=list[AgentProfileOut])
async def list_profiles(project: ApiProject, session: DBSession) -> list[AgentProfileOut]:
    return await AgentProfileService(session).list(project)


@router.get("/profiles/me", response_model=AgentProfileOut | None)
async def my_profile(request: Request, project: ApiProject, session: DBSession) -> AgentProfileOut | None:
    """The profile this key acts as — what it may read and do — or null for an unbound key."""
    profile = getattr(request.state, "agent_profile", None)
    if profile is None:
        return None
    return await AgentProfileService(session).get(project, profile.id)


@router.post("/profiles", response_model=AgentProfileOut, status_code=201, dependencies=ADMIN)
async def create_profile(payload: AgentProfileIn, project: ApiProject, session: DBSession) -> AgentProfileOut:
    return await AgentProfileService(session).create(
        project=project, payload=payload, actor_type="api_key", actor_id=project.id
    )


@router.patch("/profiles/{profile_id}", response_model=AgentProfileOut, dependencies=ADMIN)
async def update_profile(
    profile_id: str, payload: AgentProfileUpdate, project: ApiProject, session: DBSession
) -> AgentProfileOut:
    return await AgentProfileService(session).update(
        project=project, profile_id=profile_id, payload=payload, actor_type="api_key", actor_id=project.id
    )


@router.delete("/profiles/{profile_id}", response_model=Message, dependencies=ADMIN)
async def delete_profile(profile_id: str, project: ApiProject, session: DBSession) -> Message:
    await AgentProfileService(session).delete(
        project=project, profile_id=profile_id, actor_type="api_key", actor_id=project.id
    )
    return Message(message="Agent profile deleted.")


# ------------------------------------------------------------------- helpers


async def _customer(session, project_id: str, customer_id: str | None):
    if not customer_id:
        return None
    customer = await CustomerRepository(session).resolve(customer_id, project_id)
    if customer is None:
        raise NotFoundError(f"Customer '{customer_id}' not found.")
    return customer


async def _session_id(session, project_id: str, customer_id: str, session_id: str | None) -> str | None:
    found = await session_for(session, project_id, customer_id, session_id)
    return found.id if found else None
