"""ORM → schema conversion.

Kept in one place so a column rename cannot silently change the public API shape.
"""

from __future__ import annotations

from typing import Any

from app.schemas.admin import (
    ApiKeyOut,
    InvitationOut,
    MemberOut,
    WebhookDeliveryOut,
    WebhookEndpointOut,
)
from app.schemas.agents import PriorSessionOut, SessionContextOut, SessionOut, TurnOut
from app.schemas.auth import OrganizationOut, UserOut
from app.schemas.customers import CustomerOut
from app.schemas.events import EventExplanationOut, EventOut
from app.schemas.goals import GoalEvidenceOut, GoalOut
from app.schemas.memories import EntityOut, MemoryOut, MemoryVersionOut, RelationshipOut
from app.schemas.projects import ProjectOut
from app.schemas.signals import (
    RecommendationOut,
    SignalOut,
    SignalPointOut,
    SignalReportOut,
)
from app.schemas.usage import QueryLogOut
from database.models import (
    AgentSession,
    AgentTurn,
    ApiKey,
    Customer,
    CustomerGoal,
    Entity,
    Event,
    Memory,
    MemoryVersion,
    Organization,
    Project,
    QueryLog,
    Relationship,
    User,
    UserInvitation,
    WebhookDelivery,
    WebhookEndpoint,
)
from memory_engine.policy import WITHHELD


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        organization_id=user.organization_id,
        created_at=user.created_at,
    )


def organization_out(organization: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        created_at=organization.created_at,
    )


def project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
        api_key_prefix=project.api_key_prefix,
        settings=project.settings or {},
        is_active=project.is_active,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def customer_out(customer: Customer) -> CustomerOut:
    return CustomerOut(
        id=customer.id,
        project_id=customer.project_id,
        external_id=customer.external_id,
        email=customer.email,
        name=customer.name,
        metadata=customer.meta or {},
        last_event_at=customer.last_event_at,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


def event_out(event: Event, mask: Any = None) -> EventOut:
    """``mask`` is a :class:`app.services.reader.EventMask` for a reader who may not see
    everything: the payload of an event that fed a hidden memory is withheld, and so are
    the words of hidden memories named in its outcome."""
    withheld = mask is not None and event.id in mask.events
    outcome = event.outcome
    if outcome and mask is not None:
        outcome = _masked_outcome(outcome, mask.memories)
    return EventOut(
        id=event.id,
        project_id=event.project_id,
        customer_id=event.customer_id,
        event_type=event.event_type,
        external_event_id=event.external_event_id,
        data={} if withheld else (event.data or {}),
        source=event.source,
        importance=event.importance,
        status=event.status,
        occurred_at=event.occurred_at,
        created_at=event.created_at,
        processed_at=event.processed_at,
        error=event.error,
        outcome=EventExplanationOut(**outcome) if outcome else None,
        withheld=withheld,
    )


def _masked_outcome(outcome: dict[str, Any], hidden: frozenset[str]) -> dict[str, Any]:
    if not hidden:
        return outcome
    plans = []
    for plan in outcome.get("memories", []):
        plan = dict(plan)
        if plan.get("memory_id") in hidden:
            plan["content"] = WITHHELD
        if plan.get("closest_memory_id") in hidden:
            plan["closest_memory_id"] = None
            plan["closest_content"] = WITHHELD
        plans.append(plan)
    return {**outcome, "memories": plans}


def memory_out(memory: Memory, freshness: Any = None) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        project_id=memory.project_id,
        customer_id=memory.customer_id,
        type=memory.type,
        content=memory.content,
        importance=memory.importance,
        confidence=memory.confidence,
        status=memory.status,
        source=memory.source,
        sensitivity=memory.sensitivity,
        source_event_ids=list(memory.source_event_ids or []),
        evidence_count=memory.evidence_count,
        metadata=memory.meta or {},
        first_seen_at=memory.first_seen_at,
        last_seen_at=memory.last_seen_at,
        expires_at=memory.expires_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        freshness=freshness.as_dict() if freshness is not None else None,
    )


def memory_version_out(version: MemoryVersion) -> MemoryVersionOut:
    return MemoryVersionOut(
        id=version.id,
        memory_id=version.memory_id,
        previous_content=version.previous_content,
        new_content=version.new_content,
        reason=version.reason,
        source_event_id=version.source_event_id,
        created_at=version.created_at,
    )


def entity_out(entity: Entity) -> EntityOut:
    return EntityOut(
        id=entity.id,
        project_id=entity.project_id,
        type=entity.type,
        name=entity.name,
        external_id=entity.external_id,
        metadata=entity.meta or {},
        mention_count=entity.mention_count,
        created_at=entity.created_at,
    )


def relationship_out(relationship: Relationship) -> RelationshipOut:
    return RelationshipOut(
        id=relationship.id,
        source_entity_id=relationship.source_entity_id,
        relationship_type=str(relationship.relationship_type),
        target_entity_id=relationship.target_entity_id,
        confidence=relationship.confidence,
    )


def query_log_out(log: QueryLog) -> QueryLogOut:
    return QueryLogOut(
        id=log.id,
        kind=log.kind,
        query=log.query,
        answer=log.answer,
        customer_id=log.customer_id,
        memory_ids=list(log.memory_ids or []),
        event_ids=list(log.event_ids or []),
        provider=log.provider,
        model=log.model,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        latency_ms=log.latency_ms,
        created_at=log.created_at,
    )


def api_key_out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        key_prefix=key.key_prefix,
        scopes=list(key.scopes or []),
        agent_profile_id=key.agent_profile_id,
        created_by=key.created_by,
        last_used_at=key.last_used_at,
        last_used_ip=key.last_used_ip,
        use_count=key.use_count,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
        is_active=key.is_active,
        created_at=key.created_at,
    )


def member_out(user: User) -> MemberOut:
    return MemberOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def invitation_out(invitation: UserInvitation) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        invited_by=invitation.invited_by,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        created_at=invitation.created_at,
    )


def webhook_endpoint_out(endpoint: WebhookEndpoint) -> WebhookEndpointOut:
    return WebhookEndpointOut(
        id=endpoint.id,
        project_id=endpoint.project_id,
        url=endpoint.url,
        description=endpoint.description,
        event_types=list(endpoint.event_types or []),
        is_active=endpoint.is_active,
        consecutive_failures=endpoint.consecutive_failures,
        last_success_at=endpoint.last_success_at,
        last_failure_at=endpoint.last_failure_at,
        last_error=endpoint.last_error,
        created_at=endpoint.created_at,
    )


def webhook_delivery_out(delivery: WebhookDelivery) -> WebhookDeliveryOut:
    return WebhookDeliveryOut(
        id=delivery.id,
        endpoint_id=delivery.endpoint_id,
        event_type=delivery.event_type,
        event_id=delivery.event_id,
        status=delivery.status,
        attempts=delivery.attempts,
        response_status=delivery.response_status,
        response_body=delivery.response_body,
        error=delivery.error,
        duration_ms=delivery.duration_ms,
        scheduled_at=delivery.scheduled_at,
        delivered_at=delivery.delivered_at,
        created_at=delivery.created_at,
        payload=delivery.payload or {},
    )


def goal_out(goal: CustomerGoal) -> GoalOut:
    return GoalOut(
        id=goal.id,
        customer_id=goal.customer_id,
        statement=goal.statement,
        status=str(goal.status),
        progress=round(float(goal.progress or 0.0), 3),
        confidence=round(float(goal.confidence or 0.0), 3),
        keywords=list(goal.keywords or []),
        memory_id=goal.memory_id,
        evidence=[GoalEvidenceOut(**_evidence(entry)) for entry in (goal.evidence or [])],
        opened_at=goal.opened_at,
        last_signal_at=goal.last_signal_at,
        closed_at=goal.closed_at,
        closed_reason=goal.closed_reason,
        overridden=bool(goal.overridden_by),
        metadata=goal.meta or {},
    )


def _evidence(entry: dict) -> dict:
    """Only the documented keys reach the API; anything else stays in the row."""
    allowed = {"kind", "at", "memory_id", "event_id", "match", "cue", "note"}
    return {key: value for key, value in entry.items() if key in allowed}


def signal_report_out(result) -> SignalReportOut:
    """``result`` is a SignalService.CustomerSignals."""
    payload = result.as_dict()
    return SignalReportOut(
        customer_id=payload["customer_id"],
        external_id=payload["external_id"],
        name=payload.get("name"),
        trajectory=payload["trajectory"],
        churn_risk=payload["churn_risk"],
        expansion_score=payload["expansion_score"],
        confidence=payload["confidence"],
        headline=payload["headline"],
        health_score=payload["health_score"],
        signals=[SignalOut(**signal) for signal in payload["signals"]],
        measurements=payload["measurements"],
        series=[SignalPointOut(**point) for point in payload["series"]],
        computed_at=payload["computed_at"],
    )


def recommendation_out(recommendation) -> RecommendationOut:
    return RecommendationOut(**recommendation.as_dict())


def turn_out(turn: AgentTurn, mask: Any = None) -> TurnOut:
    return TurnOut(
        id=turn.id,
        role=str(turn.role),
        content=WITHHELD if mask is not None and turn.id in mask.turns else turn.content,
        occurred_at=turn.occurred_at,
        event_id=turn.event_id,
        retrieved_memory_ids=list(turn.retrieved_memory_ids or []),
    )


def prior_session_out(session: AgentSession, mask: Any = None) -> PriorSessionOut:
    return PriorSessionOut(
        id=session.id,
        agent=session.agent,
        summary=_session_summary(session, mask) or "",
        turn_count=session.turn_count,
        started_at=session.started_at,
        closed_at=session.closed_at,
    )


def session_context_out(
    context, prior: list[AgentSession] | None = None, mask: Any = None
) -> SessionContextOut:
    return SessionContextOut(
        text=context.to_prompt_text(),
        memory_ids=list(context.memory_ids),
        token_estimate=context.token_count,
        truncated=context.truncated,
        prior_sessions=[prior_session_out(session, mask) for session in (prior or [])],
    )


def _session_summary(session: AgentSession, mask: Any = None) -> str | None:
    if session.summary and mask is not None and session.id in mask.summaries:
        return WITHHELD
    return session.summary


def session_out(
    session: AgentSession,
    *,
    resumed: bool = False,
    context=None,
    prior: list[AgentSession] | None = None,
    turns: list[AgentTurn] | None = None,
    mask: Any = None,
) -> SessionOut:
    """``mask`` is a :class:`app.services.reader.SessionMask` for a reader who may not see
    everything."""
    return SessionOut(
        id=session.id,
        project_id=session.project_id,
        customer_id=session.customer_id,
        external_id=session.external_id,
        agent=session.agent,
        channel=session.channel,
        status=str(session.status),
        turn_count=session.turn_count,
        started_at=session.started_at,
        last_active_at=session.last_active_at,
        closed_at=session.closed_at,
        summary=_session_summary(session, mask),
        summary_memory_id=session.summary_memory_id,
        memory_ids=list(session.memory_ids or []),
        resumed=resumed,
        context=session_context_out(context, prior, mask) if context is not None else None,
        turns=[turn_out(turn, mask) for turn in (turns or [])],
    )
