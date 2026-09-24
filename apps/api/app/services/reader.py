"""What one reader may see of anything *derived* from memory — the rule, in one place.

Memories themselves are filtered in SQL by reader-bound repositories (§17c, §26 3.1).
Everything computed *from* them — the fact document, a goal's statement, the words a
recommendation or a signal quotes, an evaluation's retrieved snippets — is computed over
everything, because a decision must see everything, and shown through a :class:`Reader`.
The reader knows which of the ids a view cites this caller may not see: restricted ones
without clearance, and ones outside their agent profile's memory types.

Numbers are never redacted. A health score, a churn risk or a count is a statement about a
customer, not a quote from one, and two callers seeing two different scores for the same
customer would be worse than either seeing the memory.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories import GoalRepository, MemoryRepository
from memory_engine.analytics import Recommendation
from memory_engine.analytics.signals import SignalReport
from memory_engine.facts import CustomerFacts
from memory_engine.guardrails import redact_reason


@dataclass(frozen=True, slots=True)
class EventMask:
    """What to hold back when showing a page of events to a reader."""

    # Events whose payload fed a memory the reader may not see: their data is withheld.
    events: frozenset[str] = frozenset()
    # Memories named in the events' recorded outcomes that the reader may not see.
    memories: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SessionMask:
    """What to hold back when showing agent sessions to a reader."""

    # Sessions whose summary quotes a memory the reader may not see.
    summaries: frozenset[str] = frozenset()
    # Turns whose words fed a memory the reader may not see.
    turns: frozenset[str] = frozenset()


class Reader:
    def __init__(self, session: AsyncSession, *, cleared: bool) -> None:
        self.session = session
        self.cleared = cleared
        self.memories = MemoryRepository(session, cleared=cleared)
        self.goals = GoalRepository(session, cleared=cleared)

    @property
    def sees_everything(self) -> bool:
        return self.memories.sees_everything

    @property
    def restricts_types(self) -> bool:
        return self.memories.readable_types is not None

    async def hidden(self, project_id: str, ids: Iterable[str]) -> frozenset[str]:
        """Which of ``ids`` — memory or goal ids, mixed freely — this reader may not see."""
        if self.sees_everything:
            return frozenset()
        wanted = {ident for ident in ids if ident}
        if not wanted:
            return frozenset()
        return await self.memories.hidden_among(project_id, wanted) | await self.goals.hidden_among(
            project_id, wanted
        )

    # ------------------------------------------------------------------ views

    async def facts(self, project_id: str, facts: CustomerFacts) -> CustomerFacts:
        if self.sees_everything:
            return facts
        return facts.redacted(await self.hidden(project_id, facts.cited_ids()))

    async def recommendations(
        self, project_id: str, actions: list[Recommendation]
    ) -> list[Recommendation]:
        if self.sees_everything or not actions:
            return actions
        hidden = await self.hidden(project_id, {i for action in actions for i in action.cited_ids()})
        return [action.redacted(hidden) for action in actions]

    async def report(self, project_id: str, report: SignalReport) -> SignalReport:
        if self.sees_everything:
            return report
        return report.redacted(await self.hidden(project_id, report.cited_ids()))

    async def reports(self, project_id: str, reports: list[SignalReport]) -> list[SignalReport]:
        """Many customers' reports with one pair of queries, for portfolio views."""
        if self.sees_everything or not reports:
            return reports
        hidden = await self.hidden(project_id, {i for report in reports for i in report.cited_ids()})
        return [report.redacted(hidden) for report in reports]

    async def event_mask(self, project_id: str, events: Sequence[Any]) -> EventMask | None:
        """``None`` when nothing needs holding back — the common case, with no query."""
        if self.sees_everything or not events:
            return None
        named = {
            plan.get(key)
            for event in events
            for plan in (event.outcome or {}).get("memories", [])
            for key in ("memory_id", "closest_memory_id")
        }
        return EventMask(
            events=await self.memories.events_feeding_hidden(
                project_id,
                [event.id for event in events],
                customer_ids=[event.customer_id for event in events],
            ),
            memories=await self.memories.hidden_among(project_id, {i for i in named if i}),
        )

    async def session_mask(
        self, project_id: str, sessions: Sequence[Any], turns: Sequence[Any] = ()
    ) -> SessionMask | None:
        """A transcript is raw conversation and a summary is composed from it, so both are
        held back where they carry a memory the reader may not see."""
        if self.sees_everything or (not sessions and not turns):
            return None
        cited = {
            ident
            for session in sessions
            for ident in (session.summary_memory_id, *(session.memory_ids or []))
            if ident
        }
        hidden = await self.memories.hidden_among(project_id, cited)
        summaries = frozenset(
            session.id
            for session in sessions
            if session.summary_memory_id in hidden or hidden & set(session.memory_ids or [])
        )
        fed = await self.memories.events_feeding_hidden(
            project_id, [turn.event_id for turn in turns if turn.event_id]
        )
        return SessionMask(
            summaries=summaries,
            turns=frozenset(turn.id for turn in turns if turn.event_id in fed),
        )

    async def reasons(self, project_id: str, reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Guardrail reasons as this reader may see them (§26 3.2)."""
        if self.sees_everything or not reasons:
            return reasons
        hidden = await self.hidden(project_id, {i for reason in reasons for i in reason.get("evidence") or []})
        return [redact_reason(reason, hidden) for reason in reasons]

    async def visible_memory_ids(self, project_id: str, ids: Iterable[str]) -> set[str] | None:
        """The subset of ``ids`` this reader may see, or ``None`` meaning "all of them".

        Unlike :meth:`hidden`, a memory that no longer exists is *not* visible: a stored
        snippet of a deleted memory is not something to keep showing anyone but the people
        who could see everything anyway.
        """
        if self.sees_everything:
            return None
        wanted = list({ident for ident in ids if ident})
        if not wanted:
            return set()
        return {memory.id for memory in await self.memories.get_many(wanted, project_id)}

