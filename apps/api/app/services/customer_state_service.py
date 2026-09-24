"""A customer's lifecycle state and the snapshots of what we knew, kept current.

``refresh`` is the one entry point, called after every processed event and by the nightly
sweep: compute the fact document, let the lifecycle machine move the customer if its rules
say so, and record a snapshot if anything material changed. Doing all three in one place,
in that order, is what keeps them consistent — the snapshot taken after a transition
records the state the transition produced, and the transition was decided on the same
facts the snapshot records.

Manual overrides are respected, not fought: a state a person set is *pinned*, and the
machine leaves the customer alone until the pin is released or expires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.facts_service import FactsService
from common.enums import AuditAction
from common.errors import NotFoundError, ValidationError
from common.logging import get_logger
from common.time import ensure_utc, utcnow
from database.access import current_access
from database.models import Customer, CustomerSnapshot, CustomerState, Project
from database.repositories import (
    AuditRepository,
    CustomerSnapshotRepository,
    CustomerStateRepository,
)
from memory_engine.conditions import is_content_fact
from memory_engine.facts import CustomerFacts
from memory_engine.lifecycle import Lifecycle, LifecycleError, Step, compile_lifecycle
from memory_engine.policy import WITHHELD
from memory_engine.snapshots import changes as diff_changes
from memory_engine.snapshots import fingerprint, indexed
from webhooks import WebhookDispatcher, customer_state_changed

logger = get_logger(__name__)


@dataclass(slots=True)
class StateRefresh:
    facts: CustomerFacts
    state: str | None
    steps: list[Step] = field(default_factory=list)
    snapshot: CustomerSnapshot | None = None
    initial: bool = False

    @property
    def moved(self) -> bool:
        return bool(self.steps) or self.initial


def lifecycle_for(project: Project) -> Lifecycle | None:
    """The project's machine, or ``None`` if it switched lifecycle tracking off.

    A stored machine that no longer compiles (written around the API) falls back to the
    default rather than stopping every customer's state from being tracked.
    """
    raw = (project.settings or {}).get("lifecycle")
    if isinstance(raw, dict) and raw.get("enabled") is False:
        return None
    try:
        return compile_lifecycle(raw)
    except LifecycleError as exc:
        logger.error("lifecycle.invalid", project_id=project.id, error=str(exc))
        return compile_lifecycle(None)


class CustomerStateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.facts = FactsService(session)
        self.states = CustomerStateRepository(session)
        self.snapshots = CustomerSnapshotRepository(session)
        self.audit = AuditRepository(session)

    # ------------------------------------------------------------------ refresh

    async def refresh(
        self,
        *,
        project: Project,
        customer: Customer,
        reason: str = "event",
        event_id: str | None = None,
        health: Any | None = None,
        report: Any | None = None,
        emit: bool = True,
    ) -> StateRefresh:
        current = await self.states.current(project_id=project.id, customer_id=customer.id)
        now = utcnow()
        if current is not None and current.pinned and current.pinned_until and ensure_utc(current.pinned_until) <= now:
            await self.states.release_pin(current)

        facts = await self.facts.for_customer(
            project=project,
            customer=customer,
            health=health,
            report=report,
            state=current.state if current else None,
            state_entered_at=current.entered_at if current else None,
            state_pinned=bool(current and current.pinned),
        )
        result = StateRefresh(facts=facts, state=current.state if current else None)

        machine = lifecycle_for(project)
        if machine is not None and not (current and current.pinned):
            await self._advance(project, customer, machine, current, result, emit=emit)

        result.snapshot = await self._snapshot(
            project, customer, facts, reason="state_change" if result.steps else reason, event_id=event_id
        )
        return result

    async def _advance(
        self,
        project: Project,
        customer: Customer,
        machine: Lifecycle,
        current: CustomerState | None,
        result: StateRefresh,
        *,
        emit: bool,
    ) -> None:
        # A state the project's machine no longer has is treated as never placed.
        known = current.state if current and current.state in machine.states else None
        steps = machine.settle(known, result.facts)

        if known is None:
            first = steps[0].previous if steps else machine.initial
            await self.states.enter(
                project_id=project.id,
                customer_id=customer.id,
                state=first or machine.initial,
                source="initial",
                reason="Placed in the initial state the first time the lifecycle saw this customer.",
            )
            result.initial = True
            result.state = first or machine.initial
            self._set_state_facts(result.facts, result.state)

        for step in steps:
            row = await self.states.enter(
                project_id=project.id,
                customer_id=customer.id,
                state=step.target,
                source="auto",
                transition=step.transition.name,
                reason=step.reason(),
                evaluation=step.evaluation.as_dict(),
                evidence=step.evaluation.evidence,
            )
            result.state = step.target
            self._set_state_facts(result.facts, step.target)
            logger.info(
                "lifecycle.transition",
                customer_id=customer.id,
                previous=step.previous,
                state=step.target,
                transition=step.transition.name,
            )
            if emit:
                await WebhookDispatcher(self.session).emit(
                    customer_state_changed(
                        project_id=project.id,
                        customer=customer,
                        previous_state=step.previous,
                        state=step.target,
                        transition=step.transition.name,
                        source="auto",
                        reason=row.reason,
                        evidence=row.evidence,
                    )
                )
        result.steps = steps

    @staticmethod
    def _set_state_facts(facts: CustomerFacts, state: str) -> None:
        facts.values["state.current"] = state
        facts.values["state.days_in_state"] = 0.0
        facts.values["state.pinned"] = False

    async def _snapshot(
        self,
        project: Project,
        customer: Customer,
        facts: CustomerFacts,
        *,
        reason: str,
        event_id: str | None,
    ) -> CustomerSnapshot | None:
        """Record a snapshot only if something material changed since the last one."""
        print_ = fingerprint(facts)
        previous = await self.snapshots.latest(project_id=project.id, customer_id=customer.id)
        if previous is not None and previous.fingerprint == print_:
            return None
        redacted = facts.redacted()
        # The redacted view needs its own diff. The full diff compares full documents, so
        # it would say `added: ["hr"]` to a reader whose facts have "hr" removed — the
        # changes list would leak exactly what the facts were redacted to hide. Needed
        # whenever either side of the diff held restricted content.
        redacted_document = None
        if redacted is not facts or (previous is not None and previous.redacted_facts is not None):
            before = (previous.redacted_facts or previous.facts) if previous else None
            redacted_document = {**redacted.as_dict(), "changes": diff_changes(before, redacted)}
        return await self.snapshots.record(
            project_id=project.id,
            customer_id=customer.id,
            fingerprint=print_,
            facts=facts.as_dict(),
            redacted_facts=redacted_document,
            changes=diff_changes(previous.facts if previous else None, facts),
            reason=reason,
            event_id=event_id,
            indexed=indexed(facts),
        )

    # ------------------------------------------------------------------- manual

    async def set_state(
        self,
        *,
        project: Project,
        customer: Customer,
        state: str,
        actor_type: str,
        actor_id: str | None,
        pin: bool = True,
        pin_days: int | None = None,
        note: str | None = None,
    ) -> CustomerState:
        machine = lifecycle_for(project)
        if machine is None:
            raise ValidationError("Lifecycle tracking is switched off for this project.")
        target = state.strip().lower()
        if target not in machine.states:
            raise ValidationError(
                f"{state!r} is not a lifecycle state. Expected one of: {', '.join(machine.states)}."
            )
        if pin_days is not None and not 1 <= pin_days <= 365:
            raise ValidationError("pin_days must be between 1 and 365.")

        current = await self.states.current(project_id=project.id, customer_id=customer.id)
        row = await self.states.enter(
            project_id=project.id,
            customer_id=customer.id,
            state=target,
            source="manual",
            reason=note or "Set by hand.",
            pinned=pin,
            pinned_until=utcnow() + timedelta(days=pin_days) if pin and pin_days else None,
            actor_id=actor_id,
        )
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="customer_state",
            resource_id=customer.id,
            metadata={
                "event": "state_set",
                "previous": current.state if current else None,
                "state": target,
                "pinned": pin,
                "pin_days": pin_days,
            },
        )
        await WebhookDispatcher(self.session).emit(
            customer_state_changed(
                project_id=project.id,
                customer=customer,
                previous_state=current.state if current else None,
                state=target,
                transition=None,
                source="manual",
                reason=row.reason,
                evidence=[],
            )
        )
        return row

    async def release(
        self, *, project: Project, customer: Customer, actor_type: str, actor_id: str | None
    ) -> CustomerState:
        current = await self.states.current(project_id=project.id, customer_id=customer.id)
        if current is None:
            raise NotFoundError("This customer has no lifecycle state yet.")
        if not current.pinned:
            return current
        await self.states.release_pin(current)
        await self.audit.record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="customer_state",
            resource_id=customer.id,
            metadata={"event": "state_released", "state": current.state},
        )
        return current

    # --------------------------------------------------------------------- reads

    async def current(self, *, project: Project, customer: Customer) -> CustomerState | None:
        return await self.states.current(project_id=project.id, customer_id=customer.id)

    async def history(
        self, *, project: Project, customer: Customer, limit: int = 50, offset: int = 0
    ) -> tuple[list[CustomerState], int]:
        return await self.states.history(
            project_id=project.id, customer_id=customer.id, limit=limit, offset=offset
        )

    async def snapshot_at(
        self, *, project: Project, customer: Customer, moment: datetime
    ) -> CustomerSnapshot | None:
        return await self.snapshots.at(project_id=project.id, customer_id=customer.id, moment=moment)


def snapshot_view(snapshot: CustomerSnapshot, *, cleared: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The facts and the changes a reader may see from a stored snapshot.

    A reader whose agent profile reads only some memory types sees every content fact
    withheld: the stored views were redacted for clearance when the snapshot was taken, and
    cannot be re-redacted for a profile afterwards. Numbers — health, counts, risk — stay.
    """
    if cleared or snapshot.redacted_facts is None:
        view, changes = snapshot.facts, list(snapshot.changes or [])
    else:
        view = dict(snapshot.redacted_facts)
        changes = list(view.pop("changes", []))
    if current_access().restricts_types:
        return _without_content(view, changes)
    return view, changes


def _without_content(
    view: dict[str, Any], changes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = dict(view.get("values") or {})
    withheld = list(view.get("withheld_facts") or [])
    for name, value in values.items():
        if is_content_fact(name) and value not in (None, [], ""):
            values[name] = WITHHELD
            if name not in withheld:
                withheld.append(name)
    evidence = {name: ids for name, ids in (view.get("evidence") or {}).items() if not is_content_fact(name)}
    masked = []
    for change in changes:
        if is_content_fact(str(change.get("fact", ""))):
            change = {**change, "before": WITHHELD, "after": WITHHELD}
            if "added" in change or "removed" in change:
                change["added"], change["removed"] = [], []
        masked.append(change)
    return {**view, "values": values, "evidence": evidence, "withheld_facts": withheld}, masked
