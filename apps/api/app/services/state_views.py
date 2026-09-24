"""Facts, conditions, lifecycle and snapshots — shaped for a reader.

Shared by the API-key routes and the dashboard routes so the rules about what a reader
without clearance may see exist in one place. Three of them:

* a fact document is shown **redacted** (§26 1.1);
* a stored evaluation shows its rule and outcome but **withholds the actual value** of any
  fact that can quote a memory, when restriction can apply to this customer at all;
* a snapshot is shown through its stored **redacted view and redacted diff** when one was
  recorded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import Page
from app.schemas.state import (
    ConditionEvaluationOut,
    ConditionValidation,
    CurrentStateOut,
    CustomerFactsOut,
    CustomerStateOut,
    EvaluationOut,
    FactCatalogOut,
    FactSpecOut,
    LifecycleOut,
    SnapshotOut,
    SnapshotSummaryOut,
    StateRefreshOut,
)
from app.services.customer_state_service import (
    CustomerStateService,
    lifecycle_for,
    snapshot_view,
)
from app.services.facts_service import FactsService
from common.errors import NotFoundError, ValidationError
from database.access import current_access
from database.models import Customer, CustomerSnapshot, CustomerState, Project
from database.repositories import (
    CustomerSnapshotRepository,
    CustomerStateRepository,
    MemoryRepository,
)
from memory_engine.conditions import (
    ConditionError,
    compile_condition,
    fact_catalog,
    sanitize_evaluation,
)
from memory_engine.facts import METADATA_PREFIX

EXAMPLES = [
    'health.score < 60 and problems.entities contains "shopify"',
    'signals.trajectory == "declining" or intents.kinds contains "cancellation"',
    'not (subscription.plan in ["enterprise", "business"])',
    "problems.oldest_open_days between 7 and 30",
    'problems.terms contains "billing" and preferences.channel == "whatsapp"',
    'customer.metadata.segment == "smb" and activity.trend == "declining"',
]


# ---------------------------------------------------------------- conditions


def catalog() -> FactCatalogOut:
    return FactCatalogOut(
        facts=[FactSpecOut(**entry) for entry in fact_catalog()],
        metadata_prefix=METADATA_PREFIX,
        examples=EXAMPLES,
    )


def validate(condition: str | dict[str, Any]) -> ConditionValidation:
    try:
        compiled = compile_condition(condition)
    except ConditionError as exc:
        return ConditionValidation(valid=False, error=str(exc), position=exc.position)
    return ConditionValidation(valid=True, text=compiled.text, ast=compiled.ast, facts=compiled.facts)


def _compile_or_422(condition: str | dict[str, Any]):
    try:
        return compile_condition(condition)
    except ConditionError as exc:
        raise ValidationError(str(exc)) from exc


async def evaluate(
    session: AsyncSession,
    *,
    project: Project,
    customer: Customer,
    condition: str | dict[str, Any],
    cleared: bool,
) -> ConditionEvaluationOut:
    compiled = _compile_or_422(condition)
    evaluation, facts = await FactsService(session).evaluate(
        project=project, customer=customer, condition=compiled, cleared=cleared
    )
    return ConditionEvaluationOut(
        customer_id=customer.external_id,
        condition=compiled.text,
        evaluation=EvaluationOut(**evaluation.as_dict()),
        withheld_facts=facts.withheld_facts,
    )


async def customer_facts(
    session: AsyncSession, *, project: Project, customer: Customer, cleared: bool
) -> CustomerFactsOut:
    service = FactsService(session)
    facts = await service.for_customer(project=project, customer=customer)
    visible = await service.visible(project=project, facts=facts, cleared=cleared)
    return CustomerFactsOut(
        customer_id=customer.external_id,
        values=visible.values,
        evidence=visible.evidence,
        withheld_facts=visible.withheld_facts,
        computed_at=visible.computed_at,
    )


# ------------------------------------------------------------------ lifecycle


async def _sanitizing(session: AsyncSession, project: Project, customer: Customer, cleared: bool) -> bool:
    """Whether stored traces must hide actual values from this reader.

    Only where restriction can apply: a project with no restriction policy cannot have a
    restricted memory, and hiding values there would cost clarity for no protection. A
    reader whose agent profile reads only some memory types always gets the sanitised
    trace — a stored evaluation cannot be re-redacted for a rule it predates.
    """
    if current_access().restricts_types:
        return True
    if cleared:
        return False
    if (project.settings or {}).get("restriction_policies"):
        return True
    restricted = await MemoryRepository(session).restricted_ids(
        project_id=project.id, customer_id=customer.id
    )
    return bool(restricted)


def state_out(row: CustomerState, *, sanitize: bool) -> CustomerStateOut:
    evaluation = dict(row.evaluation or {})
    reason = row.reason
    if sanitize and evaluation:
        evaluation = sanitize_evaluation(evaluation)
        if row.transition:
            reason = f"{row.transition}: {evaluation.get('explanation', '')}"
    return CustomerStateOut(
        id=row.id,
        state=row.state,
        previous_state=row.previous_state,
        entered_at=row.entered_at,
        exited_at=row.exited_at,
        source=row.source,
        transition=row.transition,
        reason=reason,
        evidence=list(row.evidence or []),
        pinned=bool(row.pinned),
        pinned_until=row.pinned_until,
        actor_id=row.actor_id,
        evaluation=evaluation,
    )


async def current_state(
    session: AsyncSession, *, project: Project, customer: Customer, cleared: bool
) -> CurrentStateOut:
    machine = lifecycle_for(project)
    row = await CustomerStateRepository(session).current(project_id=project.id, customer_id=customer.id)
    sanitize = await _sanitizing(session, project, customer, cleared)
    return CurrentStateOut(
        customer_id=customer.external_id,
        enabled=machine is not None,
        current=state_out(row, sanitize=sanitize) if row else None,
        states=list(machine.states) if machine else [],
    )


async def state_history(
    session: AsyncSession,
    *,
    project: Project,
    customer: Customer,
    cleared: bool,
    limit: int,
    offset: int,
) -> Page[CustomerStateOut]:
    rows, total = await CustomerStateRepository(session).history(
        project_id=project.id, customer_id=customer.id, limit=limit, offset=offset
    )
    sanitize = await _sanitizing(session, project, customer, cleared)
    return Page[CustomerStateOut](
        data=[state_out(row, sanitize=sanitize) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def refresh_state(
    session: AsyncSession, *, project: Project, customer: Customer
) -> StateRefreshOut:
    refreshed = await CustomerStateService(session).refresh(
        project=project, customer=customer, reason="manual"
    )
    return StateRefreshOut(
        customer_id=customer.external_id,
        state=refreshed.state,
        moved=refreshed.moved,
        transitions=[
            {"from": step.previous, "to": step.target, "transition": step.transition.name}
            for step in refreshed.steps
        ],
        snapshot_id=refreshed.snapshot.id if refreshed.snapshot else None,
    )


async def lifecycle(session: AsyncSession, *, project: Project) -> LifecycleOut:
    machine = lifecycle_for(project)
    if machine is None:
        return LifecycleOut(enabled=False)
    counts = await CustomerStateRepository(session).counts_by_state(project.id)
    return LifecycleOut(
        enabled=True,
        states=list(machine.states),
        initial=machine.initial,
        transitions=[transition.as_dict() for transition in machine.transitions],
        counts={state: counts.get(state, 0) for state in machine.states},
    )


# ------------------------------------------------------------------ snapshots


def _summary(snapshot: CustomerSnapshot, *, cleared: bool) -> SnapshotSummaryOut:
    facts, changes = snapshot_view(snapshot, cleared=cleared)
    values = facts.get("values", {})
    return SnapshotSummaryOut(
        id=snapshot.id,
        taken_at=snapshot.taken_at,
        reason=snapshot.reason,
        event_id=snapshot.event_id,
        health_score=snapshot.health_score,
        health_band=snapshot.health_band,
        state=snapshot.state,
        # Read from the view, not the column: a plan known only from a restricted
        # subscription memory is withheld there.
        plan=values.get("subscription.plan"),
        trajectory=snapshot.trajectory,
        open_problems=snapshot.open_problems,
        churn_risk=snapshot.churn_risk,
        expansion_score=snapshot.expansion_score,
        changes=changes,
    )


def snapshot_out(snapshot: CustomerSnapshot, *, cleared: bool) -> SnapshotOut:
    facts, _ = snapshot_view(snapshot, cleared=cleared)
    return SnapshotOut(
        **_summary(snapshot, cleared=cleared).model_dump(),
        facts=facts.get("values", {}),
        evidence=facts.get("evidence", {}),
        withheld_facts=facts.get("withheld_facts", []),
    )


async def snapshots(
    session: AsyncSession,
    *,
    project: Project,
    customer: Customer,
    cleared: bool,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    offset: int,
) -> Page[SnapshotSummaryOut]:
    rows, total = await CustomerSnapshotRepository(session).list(
        project_id=project.id,
        customer_id=customer.id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return Page[SnapshotSummaryOut](
        data=[_summary(row, cleared=cleared) for row in rows], total=total, limit=limit, offset=offset
    )


async def snapshot_at(
    session: AsyncSession, *, project: Project, customer: Customer, moment: datetime, cleared: bool
) -> SnapshotOut:
    row = await CustomerSnapshotRepository(session).at(
        project_id=project.id, customer_id=customer.id, moment=moment
    )
    if row is None:
        raise NotFoundError("No snapshot was taken on or before that time.")
    return snapshot_out(row, cleared=cleared)


async def snapshot_detail(
    session: AsyncSession, *, project: Project, customer: Customer, snapshot_id: str, cleared: bool
) -> SnapshotOut:
    row = await CustomerSnapshotRepository(session).get(snapshot_id, project.id)
    if row is None or row.customer_id != customer.id:
        raise NotFoundError("Snapshot not found.")
    return snapshot_out(row, cleared=cleared)
