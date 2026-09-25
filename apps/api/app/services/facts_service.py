"""Gathers what the fact builder reads, from the services that own each piece.

:func:`memory_engine.facts.build_facts` is pure; this is the part that talks to the
database. It deliberately calls ``HealthService`` and ``SignalService`` rather than
recomputing health or the forecast itself, so ``health.score`` in a fact document is the
number ``/health`` returns — the same aggregation-over-re-derivation rule Customer 360
follows (§12b).

Callers that already hold a health score or a forecast — the worker, straight after
processing an event — pass them in, and nothing is computed twice.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.health_service import HealthService
from app.services.reader import Reader
from app.services.signal_service import SignalService
from common.time import utcnow
from database.models import Customer, Project
from database.repositories import (
    AgentActionRepository,
    DriftRepository,
    GoalRepository,
    MemoryRepository,
)
from memory_engine.conditions import Condition, Evaluation
from memory_engine.facts import ACTION_HISTORY_DAYS, CustomerFacts, FactInputs, build_facts
from memory_engine.freshness import gather

# Deep enough that a customer's open problems and preferences are all present — the
# fact builder reads every one of them — without reading their whole history.
PER_TYPE = 40


class FactsService:
    """Facts over *everything*, with a redacted view for readers without clearance.

    Computing through a clearance-bound repository would be the wrong kind of safe: a
    guardrail deciding whether to allow an upsell must see a restricted open problem even
    when the agent asking may not read it. So decisions use the full document, and what a
    caller is *shown* goes through :meth:`CustomerFacts.redacted`, which removes exactly
    what came only from restricted memories.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.memories = MemoryRepository(session)
        self.goals = GoalRepository(session)
        self.health = HealthService(session)
        self.signals = SignalService(session)

    async def for_customer(
        self,
        *,
        project: Project,
        customer: Customer,
        health: Any | None = None,
        report: Any | None = None,
        state: str | None = None,
        state_entered_at: Any | None = None,
        state_pinned: bool = False,
        tracks: dict[str, tuple[str | None, Any]] | None = None,
    ) -> CustomerFacts:
        if health is None:
            health = (await self.health.for_customer(project=project, customer=customer)).health
        if report is None:
            report = (
                await self.signals.for_customer(project=project, customer=customer, include_series=False)
            ).report

        grouped = await self.memories.top_by_type(
            project_id=project.id, customer_id=customer.id, per_type=PER_TYPE
        )
        goals, _ = await self.goals.list(project_id=project.id, customer_id=customer.id, limit=100)
        features = await self.memories.feature_counts_by_customer(
            project_id=project.id, customer_ids=[customer.id]
        )
        restricted = await self.memories.restricted_ids(project_id=project.id, customer_id=customer.id)
        _, total = await self.memories.list(project_id=project.id, customer_id=customer.id, limit=1)
        now = utcnow()
        # What agents were cleared to do for this customer, or reported done (§26 4.5).
        actions = await AgentActionRepository(self.session).history(
            project_id=project.id, customer_id=customer.id, since=now - timedelta(days=ACTION_HISTORY_DAYS)
        )

        # How current each memory is, and what evidence says may be out of date (§26 5.5).
        drift_repository = DriftRepository(self.session)
        freshness = await gather(
            [memory for rows in grouped.values() for memory in rows],
            project_id=project.id,
            project_settings=project.settings,
            memory_repository=self.memories,
            drift_repository=drift_repository,
            now=now,
        )
        drift = await drift_repository.open_for_customer(project_id=project.id, customer_id=customer.id)

        if state is None or tracks is None:
            loaded_state, loaded_entered, loaded_pinned, loaded_tracks = await self._state(project, customer)
            if state is None:
                state, state_entered_at, state_pinned = loaded_state, loaded_entered, loaded_pinned
            if tracks is None:
                tracks = loaded_tracks

        return build_facts(
            FactInputs(
                customer=customer,
                now=now,
                health=health,
                report=report,
                memories_by_type=grouped,
                goals=goals,
                distinct_features=features.get(customer.id, 0),
                memory_count=total,
                restricted_count=len(restricted),
                restricted_memory_ids=restricted,
                state=state,
                state_entered_at=state_entered_at,
                state_pinned=state_pinned,
                tracks=tracks or {},
                actions=actions,
                freshness=freshness,
                drift=drift,
            )
        )

    async def evaluate(
        self, *, project: Project, customer: Customer, condition: Condition, cleared: bool
    ) -> tuple[Evaluation, CustomerFacts]:
        """Evaluate a caller's condition against the facts that caller may see.

        A condition written by the caller is evaluated on the *redacted* document when the
        caller may not see everything — otherwise `problems.terms contains "salary"` would
        be a yes/no oracle for a memory they cannot read. Facts that redaction removed read
        as unknown, and the trace says so.
        """
        facts = await self.for_customer(project=project, customer=customer)
        visible = await self.visible(project=project, facts=facts, cleared=cleared)
        return condition.evaluate(visible), visible

    async def visible(self, *, project: Project, facts: CustomerFacts, cleared: bool) -> CustomerFacts:
        """``facts`` as this caller may see them: without what came only from memories
        that are restricted (and they lack clearance) or outside their agent profile."""
        return await Reader(self.session, cleared=cleared).facts(project.id, facts)

    async def _state(
        self, project: Project, customer: Customer
    ) -> tuple[str | None, Any, bool, dict[str, tuple[str | None, Any]]]:
        """The customer's state on every lifecycle track the machines have recorded; the
        primary track separately, because it is also `state.current`."""
        from database.repositories import CustomerStateRepository
        from database.repositories.customer_state import PRIMARY_TRACK

        currents = await CustomerStateRepository(self.session).currents(
            project_id=project.id, customer_id=customer.id
        )
        tracks = {name: (row.state, row.entered_at) for name, row in currents.items()}
        primary = currents.get(PRIMARY_TRACK)
        if primary is None:
            return None, None, False, tracks
        return primary.state, primary.entered_at, bool(primary.pinned), tracks
