"""What changed about a customer, and what they looked like then and now (§26 4.1).

The detection is pure (:mod:`memory_engine.changes`); this gathers what it reads — each
from the record that is authoritative for it — and applies one reader's view:

* changes about memories or goals the reader may not see are dropped and counted;
* a *before* quoting a hidden record is withheld;
* lifecycle reasons come from the evaluation as this reader may see it;
* *then* and *now* are the stored redacted snapshot view and the redacted live facts.

A window is ``since`` → ``until``. ``since`` is a span (``7d``, ``12h``, ``2w``, ``3mo``), a
time, a snapshot id, or one of two moments people actually mean by "since I last…":
``last_session`` (the last conversation with the customer) and ``last_run`` (the last
time an agent acted for them).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.changes import (
    ChangeOut,
    ChangesOut,
    CompareOut,
    CustomerAtOut,
    FactDifferenceOut,
    WindowOut,
)
from app.services.customer_state_service import snapshot_view, tracks_for
from app.services.facts_service import FactsService
from app.services.reader import Reader
from app.services.signal_service import SignalService
from app.services.state_views import sanitizing
from common.enums import AgentSessionStatus, MemoryType
from common.errors import NotFoundError, ValidationError
from common.time import ensure_utc, utcnow
from database.models import AgentSession, Customer, CustomerSnapshot, Project
from database.repositories import (
    AgentSessionRepository,
    CustomerSnapshotRepository,
    CustomerStateRepository,
    CustomerViewRepository,
    DriftRepository,
    EntityRepository,
    EventRepository,
    GoalRepository,
    MemoryRepository,
    QueryLogRepository,
)
from memory_engine.changes import (
    TYPES,
    ChangeInputs,
    cited_ids,
    counts,
    detect,
    fact_diff,
    shape,
    state_of,
    summarise,
)
from memory_engine.conditions import sanitize_evaluation
from memory_engine.lifecycle import PRIMARY_TRACK

DEFAULT_SPAN = "30d"
MAX_DAYS = 3650
MAX_CHANGES = 200
_SPAN = re.compile(r"^(\d{1,5})\s*(h|d|w|mo)$")
_UNITS = {"h": ("hour", timedelta(hours=1)), "d": ("day", timedelta(days=1)), "w": ("week", timedelta(weeks=1)), "mo": ("month", timedelta(days=30))}
# A window ending this close to the request is "now": the live facts are its end.
_LIVE_SLACK = timedelta(minutes=1)


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y}"


def _span(text: str) -> tuple[timedelta, str] | None:
    match = _SPAN.match(text)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    name, step = _UNITS[unit]
    if amount < 1:
        raise ValidationError("A span must be at least 1.")
    delta = step * amount
    if delta > timedelta(days=MAX_DAYS):
        raise ValidationError(f"A span can be at most {MAX_DAYS} days.")
    return delta, f"{amount} {name}{'s' if amount != 1 else ''}"


def _time(text: str) -> datetime | None:
    try:
        if len(text) == 10:
            parsed = date.fromisoformat(text)
            return ensure_utc(datetime(parsed.year, parsed.month, parsed.day))
        return ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00")))
    except ValueError:
        return None


@dataclass(slots=True)
class Window:
    since: datetime
    until: datetime
    basis: str
    value: str | None
    label: str
    found: bool = True
    note: str | None = None
    live: bool = True

    @property
    def span(self) -> timedelta:
        return self.until - self.since

    def out(self) -> WindowOut:
        return WindowOut(
            since=self.since,
            until=self.until,
            basis=self.basis,
            value=self.value,
            found=self.found,
            note=self.note,
            label=self.label,
        )


class ChangesService:
    def __init__(self, session: AsyncSession, *, cleared: bool) -> None:
        self.session = session
        self.cleared = cleared
        # System reads: what changed is decided over everything and shown through a
        # reader, like the fact document (§26 3.1).
        self.memories = MemoryRepository(session)
        self.goals = GoalRepository(session)
        self.states = CustomerStateRepository(session)
        self.snapshots = CustomerSnapshotRepository(session)
        self.events = EventRepository(session)
        self.reader = Reader(session, cleared=cleared)

    # ------------------------------------------------------------------ window

    async def window(
        self,
        *,
        project: Project,
        customer: Customer,
        since: str | None,
        until: str | None = None,
        agent: str | None = None,
        viewer: tuple[str, str] | None = None,
    ) -> Window:
        """``viewer`` is (``user`` or ``api_key``, id), for ``last_view``: since this person or
        key last looked at the customer."""
        now = utcnow()
        end, live, end_label = await self._until(project, customer, until, now)
        text = (since or "").strip().lower()
        if not text:
            delta, words = _span(DEFAULT_SPAN)  # type: ignore[misc]
            return Window(end - delta, end, "default", DEFAULT_SPAN, self._last(words, end_label), live=live)

        if (parsed := _span(text)) is not None:
            delta, words = parsed
            return Window(end - delta, end, "span", text, self._last(words, end_label), live=live)

        if text in ("last_session", "last_conversation"):
            session = await self._last_session(project, customer, agent=agent, before=end)
            if session is not None:
                moment = ensure_utc(session.closed_at or session.last_active_at)
                who = f" with {session.agent}" if agent else ""
                return Window(moment, end, "last_session", session.id, f"Since the last conversation{who} ({_day(moment)})", live=live)
            return self._fallback(end, live, end_label, "last_session", "No earlier conversation with this customer")

        if text in ("last_view", "last_look"):
            looked = None
            if viewer is not None:
                looked = await CustomerViewRepository(self.session).last_look(
                    customer_id=customer.id, viewer_type=viewer[0], viewer_id=viewer[1], now=now
                )
            if looked is not None and looked < end:
                return Window(looked, end, "last_view", None, f"Since you last looked ({_day(looked)})", live=live)
            return self._fallback(end, live, end_label, "last_view", "You have not looked at this customer before")

        if text == "last_run":
            runs, _ = await QueryLogRepository(self.session).runs(
                project_id=project.id, customer_id=customer.id, agent=agent, until=end, limit=1
            )
            if runs:
                moment = ensure_utc(runs[0].created_at)
                who = agent or "an agent"
                return Window(moment, end, "last_run", runs[0].id, f"Since {who} last acted ({_day(moment)})", live=live)
            return self._fallback(end, live, end_label, "last_run", "No agent has acted for this customer yet")

        if text.startswith("snp_"):
            snapshot = await self._snapshot(project, customer, since or "")
            moment = ensure_utc(snapshot.taken_at)
            return Window(moment, end, "snapshot", snapshot.id, f"Since the snapshot of {_day(moment)}", live=live)

        moment = _time(since or "")
        if moment is None:
            raise ValidationError(
                "'since' is a span such as 7d, 12h, 2w or 3mo, an ISO time, a snapshot id, "
                "'last_session', 'last_run' or 'last_view'."
            )
        if moment >= end:
            raise ValidationError("'since' must be before 'until'.")
        if end - moment > timedelta(days=MAX_DAYS):
            raise ValidationError(f"A window can be at most {MAX_DAYS} days.")
        return Window(moment, end, "time", since, f"Since {_day(moment)}", live=live)

    async def _until(
        self, project: Project, customer: Customer, until: str | None, now: datetime
    ) -> tuple[datetime, bool, str | None]:
        text = (until or "").strip().lower()
        if not text or text == "now":
            return now, True, None
        if (parsed := _span(text)) is not None:
            moment = now - parsed[0]
        elif text.startswith("snp_"):
            moment = ensure_utc((await self._snapshot(project, customer, until or "")).taken_at)
        else:
            found = _time(until or "")
            if found is None:
                raise ValidationError("'until' is an ISO time, a span back from now such as 7d, or a snapshot id.")
            moment = found
        if moment > now:
            moment = now
        live = now - moment <= _LIVE_SLACK
        return moment, live, None if live else _day(moment)

    @staticmethod
    def _last(words: str, end_label: str | None) -> str:
        return f"In the last {words}" if end_label is None else f"In the {words} to {end_label}"

    def _fallback(self, end: datetime, live: bool, end_label: str | None, basis: str, why: str) -> Window:
        delta, words = _span(DEFAULT_SPAN)  # type: ignore[misc]
        return Window(
            end - delta,
            end,
            basis,
            None,
            self._last(words, end_label),
            found=False,
            note=f"{why}; showing the last {words} instead.",
            live=live,
        )

    async def _last_session(
        self, project: Project, customer: Customer, *, agent: str | None, before: datetime
    ) -> AgentSession | None:
        """The last conversation that has ended — an open one is the conversation asking."""
        sessions, _ = await AgentSessionRepository(self.session).list(
            project_id=project.id, customer_id=customer.id, limit=50
        )
        for session in sessions:  # most recently active first
            if agent and session.agent != agent:
                continue
            if session.status == AgentSessionStatus.OPEN and session.closed_at is None:
                continue
            moment = ensure_utc(session.closed_at or session.last_active_at)
            if moment < before:
                return session
        return None

    async def _snapshot(self, project: Project, customer: Customer, snapshot_id: str) -> CustomerSnapshot:
        snapshot = await self.snapshots.get(snapshot_id.strip(), project.id)
        if snapshot is None or snapshot.customer_id != customer.id:
            raise NotFoundError(f"Snapshot '{snapshot_id}' not found for this customer.")
        return snapshot

    # ------------------------------------------------------------------ changes

    async def changes(
        self,
        *,
        project: Project,
        customer: Customer,
        window: Window,
        types: set[str] | None = None,
        order: str = "time",
        limit: int = 50,
        live: tuple[dict[str, Any], dict[str, str]] | None = None,
    ) -> ChangesOut:
        """``live`` is the customer's visible fact values and signal labels, when the caller
        already computed them (the brief does) — so they are not computed twice."""
        if types:
            unknown = types - set(TYPES)
            if unknown:
                raise ValidationError(f"Unknown change type(s): {', '.join(sorted(unknown))}. Types: {', '.join(TYPES)}.")
        if order not in ("time", "importance"):
            raise ValidationError("'order' is time or importance.")

        then_at, then_values = await self._then(project, customer, window.since)
        now_at, now_values, labels = await self._now(project, customer, window, live=live)
        inputs = await self._inputs(project, customer, window, then_values, now_values, labels)
        found = detect(inputs)
        hidden = await self.reader.hidden(project.id, cited_ids(found))
        shown, withheld = shape(found, hidden)
        if types:
            shown = [change for change in shown if change.type in types]
        if order == "importance":
            shown.sort(key=lambda change: (change.importance, change.detected_at), reverse=True)
        limit = max(1, min(limit, MAX_CHANGES))
        return ChangesOut(
            customer_id=customer.external_id,
            window=window.out(),
            summary=summarise(shown, lead=window.label),
            changes=[ChangeOut(**change.as_dict()) for change in shown[:limit]],
            counts=counts(shown),
            total=len(shown),
            truncated=len(shown) > limit,
            withheld=withheld,
            then=then_at,
            now=now_at,
        )

    async def compare(
        self, *, project: Project, customer: Customer, start: str, end: str | None = None
    ) -> CompareOut:
        """The customer at two moments, side by side, and every fact that differs."""
        window = await self.window(project=project, customer=customer, since=start, until=end)
        then_at, then_values = await self._then(project, customer, window.since)
        now_at, now_values, _ = await self._now(project, customer, window)
        differences = fact_diff(then_values, now_values or {})
        if then_values is None:
            summary = f"Nothing was recorded about this customer on or before {_day(window.since)}."
        elif not differences:
            summary = f"Nothing material differs between {_day(window.since)} and {'now' if window.live else _day(window.until)}."
        else:
            summary = f"Then: {then_at.description}. Now: {now_at.description}."
        return CompareOut(
            customer_id=customer.external_id,
            then=then_at,
            now=now_at,
            differences=[FactDifferenceOut(**entry) for entry in differences],
            summary=summary,
        )

    # ------------------------------------------------------------------ gather

    async def _then(
        self, project: Project, customer: Customer, moment: datetime
    ) -> tuple[CustomerAtOut, dict[str, Any] | None]:
        snapshot = await self.snapshots.at(project_id=project.id, customer_id=customer.id, moment=moment)
        if snapshot is None:
            return CustomerAtOut(at=moment, live=False), None
        view, _ = snapshot_view(snapshot, cleared=self.cleared)
        values = dict(view.get("values") or {})
        state = state_of(values)
        return (
            CustomerAtOut(
                at=moment,
                live=False,
                snapshot_id=snapshot.id,
                taken_at=snapshot.taken_at,
                state=state,
                description=describe(state),
            ),
            values,
        )

    async def _now(
        self,
        project: Project,
        customer: Customer,
        window: Window,
        *,
        live: tuple[dict[str, Any], dict[str, str]] | None = None,
    ) -> tuple[CustomerAtOut, dict[str, Any], dict[str, str]]:
        if not window.live:
            at, values = await self._then(project, customer, window.until)
            return at, values or {}, {}
        if live is not None:
            values, labels = dict(live[0]), dict(live[1])
            state = state_of(values)
            return CustomerAtOut(at=window.until, live=True, state=state, description=describe(state)), values, labels
        report = (
            await SignalService(self.session).for_customer(project=project, customer=customer, include_series=False)
        ).report
        facts_service = FactsService(self.session)
        facts = await facts_service.for_customer(project=project, customer=customer, report=report)
        visible = await facts_service.visible(project=project, facts=facts, cleared=self.cleared)
        values = dict(visible.values)
        state = state_of(values)
        labels = {signal.key: signal.label for signal in report.signals}
        return (
            CustomerAtOut(at=window.until, live=True, state=state, description=describe(state)),
            values,
            labels,
        )

    async def _inputs(
        self,
        project: Project,
        customer: Customer,
        window: Window,
        then_values: dict[str, Any] | None,
        now_values: dict[str, Any],
        labels: dict[str, str],
    ) -> ChangeInputs:
        scope = {"project_id": project.id, "customer_id": customer.id}
        since, until = window.since, window.until
        memories = await self.memories.first_seen_between(**scope, since=since, until=until)
        versions = await self.memories.versions_between(
            **scope,
            since=since,
            until=until,
            reasons=["repeated_evidence", "feedback_corrected", "offline_consolidation"],
            reason_prefixes=["consolidation_"],
        )
        wanted: set[str] = set()
        for memory in memories:
            meta = memory.meta or {}
            wanted.update(str(meta[key]) for key in ("supersedes", "corrects") if meta.get(key))
            if memory.superseded_by:
                wanted.add(memory.superseded_by)
        wanted.update(memory.superseded_by for _, memory in versions if memory.superseded_by)
        related = {memory.id: memory for memory in await self.memories.get_many(sorted(wanted), project.id)}

        previous_subscription = await self.memories.latest_before(**scope, type=MemoryType.SUBSCRIPTION, before=since)
        prior_feedback = await self.memories.first_seen_between(
            **scope,
            since=since - window.span,
            until=since - timedelta(microseconds=1),
            type=MemoryType.FEEDBACK,
        )
        goals, _ = await self.goals.list(**scope, limit=100)

        sanitize = await sanitizing(self.session, project, customer, self.cleared)
        states = []
        for row in await self.states.entered_between(**scope, since=since, until=until):
            evaluation = dict(row.evaluation or {})
            states.append((row, sanitize_evaluation(evaluation) if sanitize and evaluation else evaluation))
        track_labels = {PRIMARY_TRACK: "Lifecycle"}
        track_labels.update({name: track.label for name, track in tracks_for(project, include_disabled=True).items()})

        moments: dict[str, datetime] = {}
        rows, _ = await self.snapshots.list(**scope, since=since, until=until, limit=200)
        for snapshot in rows:  # newest first: the first sighting of a fact is its last move
            for entry in snapshot.changes or []:
                moments.setdefault(str(entry.get("fact")), ensure_utc(snapshot.taken_at))

        return ChangeInputs(
            since=since,
            until=until,
            memories=memories,
            related=related,
            previous_subscription=previous_subscription,
            prior_feedback=prior_feedback,
            versions=versions,
            goals=goals,
            states=states,
            track_labels=track_labels,
            then=then_values,
            now=now_values,
            moments=moments,
            signal_labels=labels,
            events_now=await self.events.count_between(**scope, since=since, until=until),
            events_before=await self.events.count_between(**scope, since=since - window.span, until=since),
            drift=await DriftRepository(self.session).detected_between(**scope, since=since, until=until),
            topic_types=await EntityRepository(self.session).types_of(
                project_id=project.id,
                names=sorted(
                    {
                        str(name)
                        for memory in [*memories, *related.values(), *(row for _, row in versions)]
                        for name in (memory.meta or {}).get("entity_names") or []
                    }
                ),
            ),
            customer_since=min(
                (moment for moment in (customer.created_at, await self.events.first_occurred_at(**scope)) if moment),
                key=ensure_utc,
                default=None,
            ),
        )


def describe(state: dict[str, Any] | None) -> str | None:
    """"At risk (54) · Pro plan · 3 open problems · lifecycle at risk" — one side of then vs now."""
    if state is None:
        return None
    parts: list[str] = []
    if state.get("health_band"):
        score = state.get("health_score")
        parts.append(
            str(state["health_band"]).replace("_", " ")
            + (f" ({float(score):.0f})" if isinstance(score, (int, float)) else "")
        )
    plan = state.get("plan")
    if plan and plan != "[withheld]":
        parts.append(f"{str(plan).title()} plan")
    problems = state.get("open_problems")
    if isinstance(problems, int):
        parts.append(f"{problems} open problem{'s' if problems != 1 else ''}")
    if state.get("lifecycle"):
        parts.append(f"lifecycle {str(state['lifecycle']).replace('_', ' ')}")
    for track, value in (state.get("tracks") or {}).items():
        if track != PRIMARY_TRACK and value:
            parts.append(f"{track.replace('_', ' ')} {str(value).replace('_', ' ')}")
    if state.get("trajectory"):
        parts.append(f"{state['trajectory']}")
    return " · ".join(parts) if parts else "nothing recorded yet"


__all__ = ["ChangesService", "Window", "describe"]
