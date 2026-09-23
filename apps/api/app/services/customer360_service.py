"""Everything worth knowing about one customer, in one call.

The endpoints underneath this are all correct and all separate: memories, health, goals,
signals, recommendations, links, events, agent sessions. An agent that needs to know who it
is talking to before it replies has to call eight of them, decide what matters, and stitch
the result together — every time, in every integration, slightly differently.

So this does it once. It is deliberately an *aggregation*, not a new source of truth: every
section comes from the service that already owns it, so a health score here is the same
number ``/health`` returns, and a recommendation here is the one ``/recommendations``
returns. If they ever disagree, this file is wrong.

Two decisions worth stating:

* **The queries run in sequence, not concurrently.** A SQLAlchemy ``AsyncSession`` is not
  safe for concurrent use, so gathering them would be a data race rather than a speed-up.
  The win available here was fewer queries, not parallel ones — hence
  :meth:`MemoryRepository.top_by_type`, which collapses the five per-type sections into one.
* **Sections are capped, and the caps are small.** The consumer is usually a language model
  with a token budget, and a 360 that returns four hundred memories is one the caller has
  to summarise before it can use it — which is the work this was supposed to save.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.goal_service import GoalService
from app.services.health_service import HealthService
from app.services.memory_service import MemoryService
from app.services.signal_service import SignalService
from common.enums import AgentSessionStatus, MemoryType
from common.time import utcnow
from database.models import Customer, Project
from database.repositories import (
    AgentSessionRepository,
    EventRepository,
    MemoryRepository,
)
from memory_engine import MemoryEngine

# Per section, and deliberately modest. Raising these is a decision about somebody's
# context window, so it is made here rather than by a query parameter nobody sets.
PER_TYPE = 12
IMPORTANT_MEMORIES = 8
ACTIVE_PROBLEMS = 8
PREFERENCES = 8
GOALS = 8
RECENT_EVENTS = 10
RECOMMENDATIONS = 5
CONVERSATIONS = 3
RELATIONSHIPS = 12

SECTIONS = (
    "health",
    "subscription",
    "active_problems",
    "goals",
    "preferences",
    "recent_activity",
    "important_memories",
    "relationships",
    "risk_signals",
    "recommended_actions",
    "recent_conversations",
)


@dataclass(slots=True)
class Customer360:
    """The assembled view. Sections not requested are absent rather than empty."""

    customer: dict[str, Any]
    generated_at: datetime
    summary: str = ""
    sections: dict[str, Any] = field(default_factory=dict)
    # Memories this caller's clearance hid, across every section.
    withheld: int = 0


class Customer360Service:
    def __init__(
        self, session: AsyncSession, engine: MemoryEngine, *, cleared: bool = True
    ) -> None:
        self.session = session
        self.engine = engine
        self.cleared = cleared
        # Content-bearing sections are clearance-bound. The aggregates below are not: a
        # health score is a number about a customer, not a quote from one, and two readers
        # seeing different scores would be worse than either seeing the memory.
        self.memories = MemoryRepository(session, cleared=cleared)
        self.memory_service = MemoryService(session, cleared=cleared)
        self.events = EventRepository(session)
        self.sessions = AgentSessionRepository(session)
        self.health = HealthService(session)
        self.signals = SignalService(session)
        self.goals = GoalService(session)

    async def build(
        self, *, project: Project, customer: Customer, include: frozenset[str] | None = None
    ) -> Customer360:
        wanted = include if include is not None else frozenset(SECTIONS)
        view = Customer360(
            customer={
                "id": customer.id,
                "external_id": customer.external_id,
                "name": customer.name,
                "email": customer.email,
                "created_at": customer.created_at,
                "last_event_at": customer.last_event_at,
                "metadata": customer.meta or {},
            },
            generated_at=utcnow(),
        )

        needs_memories = bool(
            wanted
            & {"subscription", "active_problems", "preferences", "important_memories"}
        )
        grouped: dict[str, list[Any]] = {}
        if needs_memories:
            grouped = await self.memories.top_by_type(
                project_id=project.id, customer_id=customer.id, per_type=PER_TYPE
            )
            view.withheld = await self.memories.withheld_count(
                project_id=project.id, customer_id=customer.id
            )

        if "subscription" in wanted:
            view.sections["subscription"] = self._subscription(grouped)

        if "active_problems" in wanted:
            view.sections["active_problems"] = [
                _memory_brief(memory)
                for memory in grouped.get(MemoryType.PROBLEM.value, [])[:ACTIVE_PROBLEMS]
            ]

        if "preferences" in wanted:
            view.sections["preferences"] = [
                _memory_brief(memory)
                for memory in grouped.get(MemoryType.PREFERENCE.value, [])[:PREFERENCES]
            ]

        if "important_memories" in wanted:
            view.sections["important_memories"] = self._important(grouped)

        if "health" in wanted:
            scored = await self.health.for_customer(project=project, customer=customer)
            # The service's own serialisation, not a second one. Anything else is a
            # promise that the two agree, kept by hand.
            view.sections["health"] = scored.health.as_dict()

        if wanted & {"risk_signals", "recommended_actions"}:
            actions, report = await self.signals.recommendations(
                project=project, customer=customer
            )
            if "risk_signals" in wanted:
                view.sections["risk_signals"] = {
                    "trajectory": str(report.trajectory),
                    "churn_risk": round(report.churn_risk, 3),
                    "expansion_score": round(report.expansion_score, 3),
                    "confidence": round(report.confidence, 3),
                    "observations": [signal.as_dict() for signal in report.signals],
                }
            if "recommended_actions" in wanted:
                view.sections["recommended_actions"] = [
                    action.as_dict() for action in actions[:RECOMMENDATIONS]
                ]

        if "goals" in wanted:
            goals, _ = await self.goals.list_for_customer(
                project=project, customer=customer, limit=GOALS
            )
            view.sections["goals"] = [
                {
                    "id": goal.id,
                    "statement": goal.statement,
                    "status": str(goal.status),
                    "progress": round(float(goal.progress or 0.0), 3),
                    "confidence": round(float(goal.confidence or 0.0), 3),
                    "opened_at": goal.opened_at,
                    "last_signal_at": goal.last_signal_at,
                }
                for goal in goals
            ]

        if "recent_activity" in wanted:
            events, _ = await self.events.list(
                project_id=project.id, customer_id=customer.id, limit=RECENT_EVENTS
            )
            view.sections["recent_activity"] = [
                {
                    "id": event.id,
                    "type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "importance": event.importance,
                    "status": str(event.status),
                    # The one-line explanation from §5b, so "nothing was remembered" is
                    # visible here rather than only on the Event explorer.
                    "outcome": (event.outcome or {}).get("summary"),
                }
                for event in events
            ]

        if "recent_conversations" in wanted:
            sessions, _ = await self.sessions.list(
                project_id=project.id, customer_id=customer.id, limit=CONVERSATIONS
            )
            view.sections["recent_conversations"] = [
                {
                    "id": item.id,
                    "agent": item.agent,
                    "status": str(item.status),
                    "summary": item.summary,
                    "started_at": item.started_at,
                    "closed_at": item.closed_at,
                    "turn_count": item.turn_count,
                    # ``==`` and not ``is``: the status column is a String, so a loaded row
                    # gives a plain str.
                    "is_open": str(item.status) == AgentSessionStatus.OPEN.value,
                }
                for item in sessions
            ]

        if "relationships" in wanted:
            graph = await self.memory_service.graph(project=project, customer=customer, depth=1)
            by_id = {node.id: node for node in graph.nodes}
            view.sections["relationships"] = [
                {
                    "relation": edge.label,
                    "entity": by_id[edge.target].label if edge.target in by_id else edge.target,
                    "entity_type": by_id[edge.target].type if edge.target in by_id else None,
                    "confidence": round(edge.confidence, 3),
                }
                for edge in graph.edges[:RELATIONSHIPS]
            ]

        view.summary = _summarise(view)
        return view

    # ------------------------------------------------------------------ pieces

    def _subscription(self, grouped: dict[str, list[Any]]) -> dict[str, Any] | None:
        """The current plan: the most recently *seen* subscription statement.

        Ranked by recency rather than importance, unlike every other section — a plan is a
        state, and the newest statement of a state is the true one even when an older,
        louder memory disagrees.
        """
        rows = grouped.get(MemoryType.SUBSCRIPTION.value, [])
        if not rows:
            return None
        current = max(rows, key=lambda memory: memory.last_seen_at)
        return {
            **_memory_brief(current),
            "history": [_memory_brief(memory) for memory in rows if memory.id != current.id][:3],
        }

    def _important(self, grouped: dict[str, list[Any]]) -> list[dict[str, Any]]:
        """The headline memories, whatever their type.

        Safe to merge the per-type buckets because the cap is smaller than the per-type
        depth: the true top N cannot contain more than ``PER_TYPE`` of any one type.
        """
        everything = [memory for rows in grouped.values() for memory in rows]
        everything.sort(key=lambda memory: (memory.importance, memory.last_seen_at), reverse=True)
        return [_memory_brief(memory) for memory in everything[:IMPORTANT_MEMORIES]]


def _memory_brief(memory: Any) -> dict[str, Any]:
    """A memory as an agent needs it: the statement, and how much to trust it."""
    return {
        "id": memory.id,
        "type": str(memory.type),
        "content": memory.content,
        "importance": round(memory.importance, 3),
        "confidence": round(memory.confidence, 3),
        "evidence_count": memory.evidence_count,
        "first_seen_at": memory.first_seen_at,
        "last_seen_at": memory.last_seen_at,
        "sensitivity": str(memory.sensitivity),
    }


def _summarise(view: Customer360) -> str:
    """One sentence for a log line or a prompt preamble.

    Composed from the sections that were actually built, so a filtered 360 does not claim
    to know things it did not look at.
    """
    parts: list[str] = []
    health = view.sections.get("health")
    if health:
        parts.append(f"health {health['score']:.0f}/100 ({health['band']})")
    problems = view.sections.get("active_problems")
    if problems:
        parts.append(f"{len(problems)} open problem{'s' if len(problems) != 1 else ''}")
    goals = view.sections.get("goals")
    if goals:
        open_goals = [goal for goal in goals if goal["status"] in ("open", "progressing")]
        if open_goals:
            parts.append(f"{len(open_goals)} active goal{'s' if len(open_goals) != 1 else ''}")
    subscription = view.sections.get("subscription")
    if subscription:
        parts.append("a recorded subscription change")
    if not parts:
        return "Nothing recorded for this customer yet."
    name = view.customer.get("name") or view.customer.get("external_id")
    return f"{name}: " + ", ".join(parts) + "."
