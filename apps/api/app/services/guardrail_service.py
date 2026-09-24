"""Agent guardrail checks and the approvals they ask for (§26 3.2, 3.3).

``memory_engine.guardrails`` decides; this records. A check is judged on the customer's
*full* fact document — a restricted open problem must still stop an upsell — and shown to
the caller through :class:`app.services.reader.Reader`, so the agent is told *that* a rule
objected without being handed words it may not read.

An approval covers exactly one action with exactly one request, once. The agent redeems
it by checking again with ``approval_id``: the rules are re-run on the facts as they are
*now*, an approval satisfies only ``require_approval`` objections — never a ``deny`` — and
a redeemed approval cannot be redeemed twice. Deciding one is audited and needs a person
or the ``approvals:decide`` scope, never the key that asked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent_policy import AgentCheckOut, ApprovalOut, ReasonOut
from app.services.facts_service import FactsService
from app.services.reader import Reader
from app.services.settings_service import DEFAULT_APPROVAL_TTL_HOURS, effective
from common.enums import AuditAction
from common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import utcnow
from database.models import AgentApproval, AgentCheck, AgentProfile, Customer, Project
from database.repositories import (
    AgentApprovalRepository,
    AgentCheckRepository,
    AuditRepository,
    CustomerRepository,
    CustomerSnapshotRepository,
)
from database.repositories.agent_policy import (
    APPROVED,
    EXPIRED,
    OPEN_STATUSES,
    PENDING,
    REJECTED,
    USED,
)
from memory_engine.guardrails import (
    ALLOW,
    DENY,
    REQUIRE_APPROVAL,
    GuardrailError,
    Guardrails,
    Profile,
    Reason,
    compile_guardrails,
    evidence_of,
    normalise_action,
)
from memory_engine.guardrails import check as run_rules
from webhooks import (
    WebhookDispatcher,
    agent_action_denied,
    agent_approval_decided,
    agent_approval_requested,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class CheckOutcome:
    check: AgentCheck | None  # None for a dry run
    customer: Customer
    action: str
    request: dict[str, Any]
    decision: str
    reasons: list[dict[str, Any]]  # as this reader may see them
    approval: AgentApproval | None
    agent: str | None
    profile: str | None
    snapshot_id: str | None
    session_id: str | None


def canonical(request: dict[str, Any] | None) -> str:
    """One spelling of a request, so "the same request" means the same thing everywhere."""
    return json.dumps(request or {}, sort_keys=True, default=str, separators=(",", ":"))


def profile_rule(profile: AgentProfile | None) -> Profile | None:
    if profile is None:
        return None
    return Profile(
        name=profile.name,
        allowed_actions=frozenset(profile.allowed_actions or []),
        denied_actions=frozenset(profile.denied_actions or []),
    )


class GuardrailService:
    def __init__(self, session: AsyncSession, *, cleared: bool) -> None:
        self.session = session
        self.reader = Reader(session, cleared=cleared)
        self.facts = FactsService(session)
        self.checks = AgentCheckRepository(session)
        self.approvals = AgentApprovalRepository(session)
        self.customers = CustomerRepository(session)
        self.snapshots = CustomerSnapshotRepository(session)
        self.audit = AuditRepository(session)

    # ------------------------------------------------------------------ check

    async def check(
        self,
        *,
        project: Project,
        customer: Customer,
        action: str,
        request: dict[str, Any],
        profile: AgentProfile | None = None,
        agent: str | None = None,
        api_key_id: str | None = None,
        approval_id: str | None = None,
        session_id: str | None = None,
        dry_run: bool = False,
    ) -> CheckOutcome:
        action = normalise_action(action)
        if not action:
            raise ValidationError("Name the action to check, e.g. offer_upgrade.")
        request = dict(request or {})
        guardrails, ttl_hours = self._configuration(project)

        facts = await self.facts.for_customer(project=project, customer=customer)
        verdict = run_rules(
            action=action,
            request=request,
            facts=facts,
            guardrails=guardrails,
            profile=profile_rule(profile),
        )
        reasons: list[Reason] = list(verdict.reasons)
        decision = verdict.decision

        approval: AgentApproval | None = None
        if approval_id:
            if dry_run:
                raise ValidationError("A dry run cannot redeem an approval.")
            approval, decision, extra = await self._redeem(
                project=project,
                customer=customer,
                action=action,
                request=request,
                approval_id=approval_id,
                decision=decision,
            )
            reasons = [*extra, *reasons]

        label = profile.name if profile is not None else _label(agent)
        snapshot = await self.snapshots.latest(project_id=project.id, customer_id=customer.id)
        stored = [reason.as_dict() for reason in reasons]
        check = None
        if not dry_run:
            check = await self.checks.record(
                project_id=project.id,
                customer_id=customer.id,
                action=action,
                request=request,
                decision=decision,
                reasons=stored,
                evidence=evidence_of(reasons),
                agent=label,
                api_key_id=api_key_id,
                approval_id=approval.id if approval is not None else None,
                snapshot_id=snapshot.id if snapshot else None,
                session_id=session_id,
            )
            if decision == REQUIRE_APPROVAL and approval is None:
                approval = await self._request_approval(
                    project=project,
                    customer=customer,
                    check=check,
                    action=action,
                    request=request,
                    reasons=stored,
                    agent=label,
                    ttl_hours=ttl_hours,
                )
                check.approval_id = approval.id
            if decision == DENY:
                await WebhookDispatcher(self.session).emit(
                    agent_action_denied(project_id=project.id, customer=customer, check=check)
                )
            await self.session.flush()

        logger.info(
            "agent.check",
            customer_id=customer.id,
            action=action,
            decision=decision,
            agent=label,
            rules=[reason.rule for reason in reasons],
            dry_run=dry_run,
        )
        return CheckOutcome(
            check=check,
            customer=customer,
            action=action,
            request=request,
            decision=decision,
            reasons=await self.reader.reasons(project.id, stored),
            approval=approval,
            agent=label,
            profile=profile.name if profile is not None else None,
            snapshot_id=snapshot.id if snapshot else None,
            session_id=session_id,
        )

    def _configuration(self, project: Project) -> tuple[Guardrails, int]:
        raw = effective(project).get("guardrails") or {}
        try:
            compiled = compile_guardrails(raw)
        except GuardrailError as exc:
            # Validated when saved, so this means settings were written around the API.
            # Fail towards caution: keep the built-ins, drop the unreadable project rules.
            logger.error("agent.guardrails_invalid", project_id=project.id, error=str(exc))
            compiled = Guardrails(disabled=frozenset())
        ttl = raw.get("approval_ttl_hours") if isinstance(raw, dict) else None
        return compiled, int(ttl or DEFAULT_APPROVAL_TTL_HOURS)

    # --------------------------------------------------------------- approvals

    async def _redeem(
        self,
        *,
        project: Project,
        customer: Customer,
        action: str,
        request: dict[str, Any],
        approval_id: str,
        decision: str,
    ) -> tuple[AgentApproval | None, str, list[Reason]]:
        approval = await self.approvals.get(approval_id, project.id, for_update=True)
        if approval is None or approval.customer_id != customer.id:
            raise NotFoundError(f"Approval '{approval_id}' not found for this customer.")
        if approval.action != action or canonical(approval.request) != canonical(request):
            raise ValidationError(
                "This approval was granted for a different action or request. An approval "
                "covers exactly what was asked for — ask again for anything else."
            )
        _lapse(approval)
        status = str(approval.status)

        if status == APPROVED:
            if decision == DENY:
                # A person approved an exception to a *requirement*, not to a refusal. If a
                # rule now denies, the situation has changed since they said yes.
                return approval, DENY, [
                    Reason(
                        rule="approval_not_applicable",
                        source="approval",
                        decision=ALLOW,
                        explanation="Approved earlier, but a rule now denies this action; an approval cannot override a denial.",
                    )
                ]
            approval.status = USED
            approval.used_at = utcnow()
            note = f" ({approval.note.rstrip('. ')})" if approval.note else ""
            return approval, ALLOW, [
                Reason(
                    rule="approved",
                    source="approval",
                    decision=ALLOW,
                    explanation=f"Approved by a person{note}. This approval is now used.",
                )
            ]
        if status == PENDING:
            # Nothing is decided yet, so the rules' verdict stands: still waiting if they
            # want a person, refused if they refuse, allowed if the need has passed.
            return approval, decision, [
                Reason(
                    rule="approval_pending",
                    source="approval",
                    decision=REQUIRE_APPROVAL if decision == REQUIRE_APPROVAL else ALLOW,
                    explanation="Still waiting for a person to decide.",
                )
            ]
        if status == REJECTED:
            note = f": {approval.note}" if approval.note else "."
            return approval, DENY, [
                Reason(
                    rule="approval_rejected",
                    source="approval",
                    decision=DENY,
                    explanation=f"A person rejected this request{note}",
                )
            ]
        # Expired or already used: as if there were none, and say so.
        return None, decision, [
            Reason(
                rule=f"approval_{status}",
                source="approval",
                decision=ALLOW,
                explanation=(
                    "That approval has expired; a new request has been filed."
                    if status == EXPIRED and decision == REQUIRE_APPROVAL
                    else f"That approval is {status} and no longer covers this action."
                ),
            )
        ]

    async def _request_approval(
        self,
        *,
        project: Project,
        customer: Customer,
        check: AgentCheck,
        action: str,
        request: dict[str, Any],
        reasons: list[dict[str, Any]],
        agent: str | None,
        ttl_hours: int,
    ) -> AgentApproval:
        """File a request for a person — or hand back the one already filed for exactly
        this, so an agent that retries does not flood the queue."""
        wanted = canonical(request)
        for existing in await self.approvals.open_for(
            project_id=project.id, customer_id=customer.id, action=action, agent=agent
        ):
            if canonical(existing.request) == wanted:
                return existing
        approval = await self.approvals.create(
            project_id=project.id,
            customer_id=customer.id,
            check_id=check.id,
            action=action,
            request=request,
            reasons=[reason for reason in reasons if reason.get("decision") != ALLOW],
            agent=agent,
            expires_at=utcnow() + timedelta(hours=ttl_hours),
        )
        await WebhookDispatcher(self.session).emit(
            agent_approval_requested(project_id=project.id, customer=customer, approval=approval)
        )
        return approval

    async def decide(
        self,
        *,
        project: Project,
        approval_id: str,
        approve: bool,
        note: str | None,
        actor_type: str,
        actor_id: str | None,
        api_key_id: str | None = None,
    ) -> AgentApproval:
        approval = await self.approvals.get(approval_id, project.id, for_update=True)
        if approval is None:
            raise NotFoundError(f"Approval '{approval_id}' not found.")
        if _lapse(approval):
            await self._announce(project, approval)
        if str(approval.status) != PENDING:
            raise ConflictError(f"This request is already {approval.status}; it cannot be decided again.")
        if api_key_id is not None:
            origin = await self.checks.get(approval.check_id, project.id)
            if origin is not None and origin.api_key_id == api_key_id:
                raise AuthorizationError("An approval cannot be decided with the key that asked for it.")

        approval.status = APPROVED if approve else REJECTED
        approval.note = (note or "").strip() or None
        approval.decided_by = actor_id
        approval.decided_at = utcnow()
        await self.session.flush()
        await self.audit.record(
            project_id=project.id,
            organization_id=project.organization_id,
            action=AuditAction.AGENT_APPROVAL,
            actor_type=actor_type,
            actor_id=actor_id,
            resource_type="agent_approval",
            resource_id=approval.id,
            metadata={
                "decision": str(approval.status),
                "action": approval.action,
                "customer_id": approval.customer_id,
                "agent": approval.agent,
                "note": approval.note,
            },
        )
        await self._announce(project, approval)
        logger.info("agent.approval_decided", approval_id=approval.id, status=str(approval.status))
        return approval

    async def expire_due(self, project: Project | None = None) -> int:
        """Lapse overdue approvals and tell whoever is listening. Used by the worker."""
        expired = await self.approvals.expire_due(project_id=project.id if project else None)
        for approval in expired:
            customer = await self.customers.get(approval.customer_id, approval.project_id)
            await WebhookDispatcher(self.session).emit(
                agent_approval_decided(project_id=approval.project_id, customer=customer, approval=approval)
            )
        return len(expired)

    async def _announce(self, project: Project, approval: AgentApproval) -> None:
        customer = await self.customers.get(approval.customer_id, project.id)
        await WebhookDispatcher(self.session).emit(
            agent_approval_decided(project_id=project.id, customer=customer, approval=approval)
        )

    # ------------------------------------------------------------------ reads

    async def list_approvals(
        self,
        *,
        project: Project,
        status: str | None = None,
        customer: Customer | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalOut], int]:
        # The queue must not show a request as pending after it lapsed.
        for approval in await self.approvals.expire_due(project_id=project.id):
            await self._announce(project, approval)
        rows, total = await self.approvals.list(
            project_id=project.id,
            status=status,
            customer_id=customer.id if customer else None,
            limit=limit,
            offset=offset,
        )
        return [await self.approval_out(project, row) for row in rows], total

    async def get_approval(self, *, project: Project, approval_id: str) -> ApprovalOut:
        approval = await self.approvals.get(approval_id, project.id)
        if approval is None:
            raise NotFoundError(f"Approval '{approval_id}' not found.")
        if _lapse(approval):
            await self._announce(project, approval)
        return await self.approval_out(project, approval)

    async def list_checks(
        self,
        *,
        project: Project,
        customer: Customer | None = None,
        decision: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AgentCheckOut], int]:
        rows, total = await self.checks.list(
            project_id=project.id,
            customer_id=customer.id if customer else None,
            decision=decision,
            agent=agent,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )
        return [await self.stored_check_out(project, row) for row in rows], total

    async def get_check(self, *, project: Project, check_id: str) -> AgentCheckOut:
        row = await self.checks.get(check_id, project.id)
        if row is None:
            raise NotFoundError(f"Check '{check_id}' not found.")
        return await self.stored_check_out(project, row)

    # ------------------------------------------------------------- serialising

    async def _external_ids(self, project: Project, ids: set[str]) -> dict[str, str]:
        customers = await self.customers.get_many(list(ids), project.id)
        return {customer.id: customer.external_id for customer in customers}

    async def approval_out(self, project: Project, approval: AgentApproval) -> ApprovalOut:
        external = await self._external_ids(project, {approval.customer_id})
        reasons = await self.reader.reasons(project.id, list(approval.reasons or []))
        return ApprovalOut(
            id=approval.id,
            customer_id=external.get(approval.customer_id, approval.customer_id),
            check_id=approval.check_id,
            agent=approval.agent,
            action=approval.action,
            request=approval.request or {},
            reasons=[ReasonOut(**reason) for reason in reasons],
            status=str(approval.status),
            note=approval.note,
            decided_by=approval.decided_by,
            decided_at=approval.decided_at,
            used_at=approval.used_at,
            expires_at=approval.expires_at,
            created_at=approval.created_at,
        )

    async def outcome_out(self, project: Project, outcome: CheckOutcome) -> AgentCheckOut:
        return AgentCheckOut(
            id=outcome.check.id if outcome.check else "dry_run",
            customer_id=outcome.customer.external_id,
            action=outcome.action,
            decision=outcome.decision,
            allowed=outcome.decision == ALLOW,
            summary=summary_of(outcome.decision, outcome.reasons),
            reasons=[ReasonOut(**reason) for reason in outcome.reasons],
            evidence=_evidence(outcome.reasons),
            approval=await self.approval_out(project, outcome.approval) if outcome.approval else None,
            agent=outcome.agent,
            profile=outcome.profile,
            request=outcome.request,
            snapshot_id=outcome.snapshot_id,
            session_id=outcome.session_id,
            checked_at=outcome.check.created_at if outcome.check else utcnow(),
        )

    async def stored_check_out(self, project: Project, check: AgentCheck) -> AgentCheckOut:
        external = await self._external_ids(project, {check.customer_id})
        reasons = await self.reader.reasons(project.id, list(check.reasons or []))
        approval = await self.approvals.get(check.approval_id, project.id) if check.approval_id else None
        return AgentCheckOut(
            id=check.id,
            customer_id=external.get(check.customer_id, check.customer_id),
            action=check.action,
            decision=check.decision,
            allowed=check.decision == ALLOW,
            summary=summary_of(check.decision, reasons),
            reasons=[ReasonOut(**reason) for reason in reasons],
            evidence=_evidence(reasons),
            approval=await self.approval_out(project, approval) if approval else None,
            agent=check.agent,
            request=check.request or {},
            snapshot_id=check.snapshot_id,
            session_id=check.session_id,
            checked_at=check.created_at,
        )


def summary_of(decision: str, reasons: list[dict[str, Any]]) -> str:
    """The sentence an agent can repeat: the first reason that decided it."""
    for reason in reasons:
        if reason.get("decision") == decision and decision != ALLOW:
            return str(reason.get("explanation", ""))
    for reason in reasons:
        if reason.get("rule") == "approved":
            return str(reason.get("explanation", ""))
    return "Allowed: no rule objects." if decision == ALLOW else "Not allowed."


def _evidence(reasons: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(ident for reason in reasons for ident in reason.get("evidence") or []))


def _label(agent: str | None) -> str | None:
    return " ".join((agent or "").split())[:120] or None


def _lapse(approval: AgentApproval) -> bool:
    """Expire an open approval past its time. Returns whether it changed."""
    if str(approval.status) in OPEN_STATUSES and approval.expires_at <= utcnow():
        approval.status = EXPIRED
        return True
    return False
