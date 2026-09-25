"""A customer's journey, gathered and shaped for one reader (§26 6.6).

The detection is pure (:mod:`memory_engine.journey`); this gathers what it reads — over the
customer's whole history, each from the record that is authoritative for it — and applies
one reader's view the way "what changed" does (§26 4.1):

* milestones about memories or goals the reader may not see are dropped and counted;
* an older statement a milestone quotes ("was: …") is left out when it is hidden;
* snapshots are read through their stored redacted views, lifecycle reasons through the
  evaluations as the reader may see them;
* a first use named only by an event is left out when that event fed a hidden memory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.changes_service import ChangesService
from app.services.customer_state_service import snapshot_view, tracks_for
from app.services.reader import Reader
from app.services.state_views import sanitizing
from common.enums import MemoryType
from common.errors import ValidationError
from common.time import ensure_utc, utcnow
from database.models import Customer, Project
from database.repositories import (
    CustomerSnapshotRepository,
    CustomerStateRepository,
    EntityRepository,
    EventRepository,
    GoalRepository,
    MemoryRepository,
)
from memory_engine.analytics.health import resolve_weights
from memory_engine.conditions import sanitize_evaluation
from memory_engine.journey import (
    CATEGORIES,
    QUIET_DAYS,
    FirstUse,
    Gap,
    JourneyInputs,
    Point,
    Stay,
    cited_ids,
    counts,
    detect,
    markdown,
    select,
    shape,
    summarise,
    within,
)
from memory_engine.lifecycle import PRIMARY_TRACK
from nlp.templates import display_name

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
# How much history is read. A customer with more is shown their latest chapters.
MAX_MEMORIES = 2000
MAX_VERSIONS = 5000
MAX_SNAPSHOTS = 2000
BEGINNING = datetime(1970, 1, 1, tzinfo=UTC)
# The memory types a milestone can come from: the rest (summaries restate, behaviour is
# read from the events themselves) only ever add noise.
TYPES = (
    MemoryType.PROBLEM, MemoryType.SUBSCRIPTION, MemoryType.INTENT, MemoryType.PREFERENCE,
    MemoryType.FEEDBACK, MemoryType.RELATIONSHIP, MemoryType.FACT, MemoryType.GOAL, MemoryType.BEHAVIOR,
)


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y}"


class JourneyService:
    def __init__(self, session: AsyncSession, *, cleared: bool) -> None:
        self.session = session
        self.cleared = cleared
        # System reads: the journey is decided over everything and shown through a reader.
        self.memories = MemoryRepository(session)
        self.goals = GoalRepository(session)
        self.states = CustomerStateRepository(session)
        self.snapshots = CustomerSnapshotRepository(session)
        self.events = EventRepository(session)
        self.reader = Reader(session, cleared=cleared)

    async def build(
        self,
        *,
        project: Project,
        customer: Customer,
        since: str | None = None,
        until: str | None = None,
        categories: set[str] | None = None,
        limit: int = DEFAULT_LIMIT,
        min_importance: float = 0.0,
    ) -> dict[str, Any]:
        if categories:
            unknown = categories - set(CATEGORIES)
            if unknown:
                raise ValidationError(
                    f"Unknown milestone categor{'y' if len(unknown) == 1 else 'ies'}: {', '.join(sorted(unknown))}. "
                    f"Categories: {', '.join(CATEGORIES)}."
                )
        if not 0.0 <= min_importance <= 1.0:
            raise ValidationError("'min_importance' is between 0 and 1.")
        limit = max(1, min(limit, MAX_LIMIT))
        now = utcnow()
        scope = {"project_id": project.id, "customer_id": customer.id}

        first = await self.events.first_event(**scope)
        beginning = min(
            (moment for moment in (customer.created_at, first[2] if first else None) if moment),
            key=ensure_utc,
            default=None,
        )
        window = await self._window(project, customer, since, until, beginning, now)

        inputs = await self._inputs(project, customer, now, first, beginning)
        found = detect(inputs)
        hidden = await self.reader.hidden(project.id, cited_ids(found))
        shown, withheld = shape(found, hidden)
        shown, dropped = await self._without_hidden_events(project, customer, shown)
        withheld += dropped

        shown = within(shown, window["since"], window["until"])
        if categories:
            shown = [milestone for milestone in shown if milestone.category in categories]
        chosen, qualified = select(shown, limit=limit, min_importance=min_importance)

        lead = f"Customer since {_day(ensure_utc(beginning))}" if window["basis"] == "all" and beginning else window["label"]
        return {
            "customer_id": customer.external_id,
            "customer": {"id": customer.id, "external_id": customer.external_id, "name": customer.name},
            "customer_since": beginning,
            "window": window,
            "summary": summarise(
                [milestone for milestone in shown if milestone.importance >= min_importance], lead=lead
            ),
            "milestones": [milestone.as_dict() for milestone in chosen],
            "counts": counts(milestone for milestone in shown if milestone.importance >= min_importance),
            "total": qualified,
            "truncated": qualified > len(chosen),
            "withheld": withheld,
            "generated_at": now,
        }

    async def markdown(self, **kwargs: Any) -> str:
        journey = await self.build(**kwargs)
        return markdown(journey)

    # ------------------------------------------------------------------ gather

    async def _window(
        self,
        project: Project,
        customer: Customer,
        since: str | None,
        until: str | None,
        beginning: datetime | None,
        now: datetime,
    ) -> dict[str, Any]:
        """The whole history by default; otherwise any window "what changed" understands."""
        if not (since or "").strip() and not (until or "").strip():
            return {
                "since": beginning,
                "until": now,
                "basis": "all",
                "label": f"Since {_day(ensure_utc(beginning))}" if beginning else "Everything so far",
                "note": None,
            }
        start = (since or "").strip() or (ensure_utc(beginning).isoformat() if beginning else (now - timedelta(days=30)).isoformat())
        window = await ChangesService(self.session, cleared=self.cleared).window(
            project=project, customer=customer, since=start, until=until
        )
        return {
            "since": window.since,
            "until": window.until,
            "basis": window.basis if (since or "").strip() else "time",
            "label": window.label,
            "note": window.note,
        }

    async def _inputs(
        self,
        project: Project,
        customer: Customer,
        now: datetime,
        first: tuple[str, str, datetime] | None,
        beginning: datetime | None,
    ) -> JourneyInputs:
        scope = {"project_id": project.id, "customer_id": customer.id}
        memories = await self.memories.history(**scope, types=[kind.value for kind in TYPES], limit=MAX_MEMORIES)
        versions = await self.memories.versions_between(
            **scope,
            since=BEGINNING,
            until=now,
            reasons=["repeated_evidence"],
            reason_prefixes=["consolidation_"],
            limit=MAX_VERSIONS,
        )
        wanted: set[str] = set()
        for memory in memories:
            meta = memory.meta or {}
            wanted.update(str(meta[key]) for key in ("supersedes", "corrects") if meta.get(key))
            if memory.superseded_by:
                wanted.add(memory.superseded_by)
        known = {memory.id for memory in memories}
        related = {
            memory.id: memory for memory in await self.memories.get_many(sorted(wanted - known), project.id)
        }
        related.update({memory.id: memory for memory in memories if memory.id in wanted})
        goals, _ = await self.goals.list(**scope, limit=200)

        sanitize = await sanitizing(self.session, project, customer, self.cleared)
        stays = []
        for row in await self.states.entered_between(**scope, since=BEGINNING, until=now):
            evaluation = dict(row.evaluation or {})
            stays.append(Stay(row, sanitize_evaluation(evaluation) if sanitize and evaluation else evaluation))

        points = []
        for snapshot in await self.snapshots.chronological(**scope, limit=MAX_SNAPSHOTS):
            view, changes = snapshot_view(snapshot, cleared=self.cleared)
            points.append(
                Point(
                    id=snapshot.id,
                    taken_at=snapshot.taken_at,
                    event_id=snapshot.event_id,
                    values=dict(view.get("values") or {}),
                    changes=list(changes),
                )
            )

        first_uses = await self._first_uses(project, customer, memories)
        gaps = await self._gaps(project, customer, now)

        chained = {point.event_id for point in points if point.event_id}
        chained.update(str(memory.source_event_ids[0]) for memory in memories if memory.source_event_ids)
        chained.update(str(version.source_event_id) for version, _ in versions if version.source_event_id)
        events = await self.events.occurred(project.id, sorted(chained))

        labels = {PRIMARY_TRACK: "Lifecycle"}
        labels.update({name: track.label for name, track in tracks_for(project, include_disabled=True).items()})
        named = {str(name) for memory in [*memories, *related.values()] for name in (memory.meta or {}).get("entity_names") or []}
        return JourneyInputs(
            now=now,
            customer_name=customer.name or customer.external_id,
            customer_since=beginning,
            first_event=first,
            memories=memories,
            related=related,
            versions=versions,
            goals=goals,
            stays=stays,
            points=points,
            events=events,
            first_uses=first_uses,
            gaps=gaps,
            track_labels=labels,
            weights=resolve_weights((project.settings or {}).get("health_weights")),
            topic_types=await EntityRepository(self.session).types_of(project_id=project.id, names=sorted(named)),
        )

    async def _first_uses(self, project: Project, customer: Customer, memories: list[Any]) -> list[FirstUse]:
        """First uses, from the events themselves — and, for what no event names, from what
        the customer said they use."""
        found: dict[str, FirstUse] = {}
        for kind, key, name, event_id, first_at, last_at, uses, event_type in await self.events.first_uses(
            project_id=project.id, customer_id=customer.id
        ):
            if key not in found:
                found[key] = FirstUse(kind, display_name(name), first_at, event_id, last_at, uses, event_type)
        for memory in memories:
            if str(memory.type) not in (MemoryType.BEHAVIOR.value, MemoryType.FACT.value):
                continue
            meta = memory.meta or {}
            name = meta.get("integration") or meta.get("feature")
            if not name or not memory.source_event_ids:
                continue
            key = " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())
            if key in found:
                continue
            found[key] = FirstUse(
                "integration" if meta.get("integration") else "feature",
                str(name),
                memory.first_seen_at,
                str(memory.source_event_ids[0]),
                memory.last_seen_at,
                int(memory.evidence_count or 1),
            )
        return sorted(found.values(), key=lambda use: ensure_utc(use.first_at))

    async def _gaps(self, project: Project, customer: Customer, now: datetime) -> list[Gap]:
        scope = {"project_id": project.id, "customer_id": customer.id}
        gaps = [
            Gap(previous_at, previous_id, returned_at, returned_id, returned_type)
            for previous_id, previous_at, returned_id, returned_at, returned_type in await self.events.quiet_gaps(
                **scope, min_days=QUIET_DAYS
            )
        ]
        last = await self.events.last_event(**scope)
        if last is not None and now - ensure_utc(last[2]) >= timedelta(days=QUIET_DAYS):
            gaps.append(Gap(last[2], last[0], None))
        return gaps

    async def _without_hidden_events(self, project: Project, customer: Customer, milestones: list[Any]) -> tuple[list[Any], int]:
        """A first use is named by its event's payload — which also fed whatever memory that
        event produced. When the reader may not see that memory, the name is its words."""
        uses = {milestone.event_id for milestone in milestones if milestone.category == "usage" and milestone.event_id}
        if not uses:
            return milestones, 0
        hidden = await MemoryRepository(self.session, cleared=self.cleared).events_feeding_hidden(
            project.id, sorted(uses), customer_ids=[customer.id]
        )
        if not hidden:
            return milestones, 0
        kept = [
            milestone
            for milestone in milestones
            if not (milestone.category == "usage" and milestone.event_id in hidden)
        ]
        return kept, len(milestones) - len(kept)


__all__ = ["JourneyService"]
