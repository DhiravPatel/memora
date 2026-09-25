"""Drift, gathered and acted on (§26 5.5).

Detection reads the records that are authoritative for each question — inbound contacts
and agent sessions for a channel, billing events for a plan, feature events for a habit,
the event stream for "stayed active" — over *everything*, like the fact document: a flag
is a statement about the customer, and a reader who may not see the memory is simply not
shown the flag.

Nothing here changes a memory on its own. Confirming a flag writes the change through the
memory repository — a new statement, the old one superseded or expired, versions and audit
included — and dismissing it keeps the memory and restarts the count from the dismissal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.reader import Reader
from common.enums import AuditAction, MemorySource, MemoryStatus, MemoryType, Sensitivity
from common.errors import ConflictError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import ensure_utc, utcnow
from database.models import Customer, Memory, MemoryDrift, Project
from database.repositories import (
    AgentSessionRepository,
    AuditRepository,
    CustomerRepository,
    DriftRepository,
    EventRepository,
    MemoryRepository,
)
from database.repositories.drift import CLEARED, CONFIRMED, DISMISSED, OPEN, STATUSES
from memory_engine.consolidation.rules import is_transition
from memory_engine.drift import (
    KIND_LABELS,
    KINDS,
    Billing,
    Contact,
    Finding,
    channel_drift,
    plan_drift,
    quiet_problem,
    settings_for,
    usage_drift,
)
from memory_engine.facts import plan_of, plans_named, preferred_channel
from memory_engine.freshness import day, evidence_at, span
from memory_engine.policy import classify
from nlp.entities import channel_named, contact_channel, display_channel
from webhooks import (
    WebhookDispatcher,
    memory_conflict,
    memory_created,
    memory_drift_detected,
    memory_drift_resolved,
)

logger = get_logger(__name__)

# Memories of each type examined per customer: the same depth the fact document reads.
PER_TYPE = 40
# Events read per pass. A pattern shows in the newest thousand.
EVENT_LIMIT = 1000
# A conversation that arrived as an event is one contact, not two.
SAME_CONTACT = timedelta(minutes=10)
# Billing events: the ones that name a plan without anyone saying anything.
_BILLING = ("invoice", "payment", "charge", "billing", "renewal")
_USES_FEATURE = re.compile(r"\buses the (.+?) feature\b", re.IGNORECASE)
_PLAN_ORDER = ("free", "trial", "lite", "starter", "basic", "standard", "plus", "team", "growth",
               "pro", "professional", "premium", "business", "scale", "enterprise")


@dataclass(slots=True)
class DriftRun:
    opened: list[MemoryDrift] = field(default_factory=list)
    refreshed: list[MemoryDrift] = field(default_factory=list)
    cleared: list[MemoryDrift] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "opened": [flag.id for flag in self.opened],
            "refreshed": [flag.id for flag in self.refreshed],
            "cleared": [flag.id for flag in self.cleared],
        }


class DriftService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True, embedder: Any | None = None) -> None:
        """``cleared`` shapes what a caller is shown; detection always reads everything."""
        self.session = session
        self.cleared = cleared
        self.embedder = embedder
        self.memories = MemoryRepository(session)
        self.drift = DriftRepository(session)
        self.events = EventRepository(session)
        self.sessions = AgentSessionRepository(session)
        self.customers = CustomerRepository(session)
        self.reader = Reader(session, cleared=cleared)

    # ----------------------------------------------------------------- detect

    async def detect(
        self,
        *,
        project: Project,
        customer: Customer,
        kinds: tuple[str, ...] = KINDS,
        now: datetime | None = None,
        emit: bool = True,
    ) -> DriftRun:
        """Run the detectors for one customer: open or refresh the flags that hold, clear the
        ones this pass looked at that no longer do."""
        now = now or utcnow()
        unknown = set(kinds) - set(KINDS)
        if unknown:
            raise ValidationError(f"Unknown drift kind(s): {', '.join(sorted(unknown))}. Kinds: {', '.join(KINDS)}.")
        settings = settings_for(project.settings)
        grouped = await self.memories.top_by_type(project_id=project.id, customer_id=customer.id, per_type=PER_TYPE)
        dismissed = await self.drift.last_dismissals(project_id=project.id, customer_id=customer.id)
        findings: list[Finding] = []
        considered: set[tuple[str, str]] = set()

        def restart(memory_id: str, kind: str, moment: datetime) -> datetime:
            after = dismissed.get((memory_id, kind))
            return max(ensure_utc(moment), ensure_utc(after)) if after is not None else ensure_utc(moment)

        if "channel" in kinds:
            stated, memory_id, _ = preferred_channel(grouped.get(MemoryType.PREFERENCE.value, []))
            memory = next((row for row in grouped.get(MemoryType.PREFERENCE.value, []) if row.id == memory_id), None)
            if stated and memory is not None:
                stated_at = evidence_at(memory)
                since = restart(memory.id, "channel", stated_at)
                considered.add((memory.id, "channel"))
                found = channel_drift(
                    memory_id=memory.id,
                    stated=stated,
                    since=since,
                    stated_at=stated_at,
                    contacts=await self._contacts(project, customer, since),
                    settings=settings,
                )
                if found is not None:
                    findings.append(found)

        if "plan" in kinds:
            subscriptions = grouped.get(MemoryType.SUBSCRIPTION.value, [])
            if subscriptions:
                current = max(subscriptions, key=lambda row: ensure_utc(row.last_seen_at))
                plan, direction = plan_of(current)
                if plan and direction != "cancelled":
                    stated_at = evidence_at(current)
                    since = restart(current.id, "plan", stated_at)
                    considered.add((current.id, "plan"))
                    found = plan_drift(
                        memory_id=current.id,
                        stated=plan,
                        since=since,
                        stated_at=stated_at,
                        billing=await self._billing(project, customer, since),
                        settings=settings,
                    )
                    if found is not None:
                        findings.append(found)

        if "usage" in kinds:
            seen_features: set[str] = set()
            for memory in grouped.get(MemoryType.BEHAVIOR.value, []):
                feature = feature_of(memory)
                if not feature or feature.lower() in seen_features:
                    continue
                seen_features.add(feature.lower())
                last = await self.events.last_feature_use(project_id=project.id, customer_id=customer.id, feature=feature)
                if last is None:
                    # No feature events at all: nothing says whether they still use it.
                    continue
                considered.add((memory.id, "usage"))
                counting = restart(memory.id, "usage", last)
                found = usage_drift(
                    memory_id=memory.id,
                    feature=feature,
                    last_used_at=last,
                    counting_from=counting,
                    activity_since=await self._activity(project, customer, counting, now),
                    settings=settings,
                    now=now,
                )
                if found is not None:
                    findings.append(found)

        if "quiet_problem" in kinds:
            for memory in grouped.get(MemoryType.PROBLEM.value, []):
                evidenced = evidence_at(memory)
                if (now - evidenced).days < settings.quiet_problem_days:
                    considered.add((memory.id, "quiet_problem"))
                    continue  # reported recently: nothing to count
                counting = restart(memory.id, "quiet_problem", evidenced)
                considered.add((memory.id, "quiet_problem"))
                found = quiet_problem(
                    memory_id=memory.id,
                    content=memory.content,
                    evidence_at=evidenced,
                    counting_from=counting,
                    activity_since=await self._activity(project, customer, counting, now),
                    settings=settings,
                    now=now,
                )
                if found is not None:
                    findings.append(found)

        run = DriftRun()
        for finding in findings:
            flag, new = await self.drift.upsert_open(
                project_id=project.id,
                customer_id=customer.id,
                memory_id=finding.memory_id,
                kind=finding.kind,
                stated=finding.stated,
                observed=finding.observed,
                summary=finding.summary,
                counts=finding.counts,
                evidence=finding.evidence,
                since=finding.since,
            )
            (run.opened if new else run.refreshed).append(flag)

        found = {(finding.memory_id, finding.kind) for finding in findings}
        open_flags = await self.drift.open_for_customer(project_id=project.id, customer_id=customer.id)
        standing = {
            row.id
            for row in await self.memories.get_many([flag.memory_id for flag in open_flags], project.id)
            if str(row.status) == MemoryStatus.ACTIVE.value
        }
        for flag in open_flags:
            key = (flag.memory_id, flag.kind)
            if key in found:
                continue
            if flag.memory_id not in standing:
                note = "The memory it was about is no longer standing."
            elif key in considered:
                note = "The evidence no longer points the other way."
            else:
                continue
            await self.drift.resolve(flag, status=CLEARED, by=None, by_type="system", note=note)
            run.cleared.append(flag)

        if emit and (run.opened or run.cleared):
            dispatcher = WebhookDispatcher(self.session)
            for flag in run.opened:
                await dispatcher.emit(memory_drift_detected(project_id=project.id, customer=customer, drift=flag))
            for flag in run.cleared:
                await dispatcher.emit(memory_drift_resolved(project_id=project.id, customer=customer, drift=flag))
        if run.opened or run.cleared:
            logger.info(
                "memory.drift",
                customer_id=customer.id,
                opened=[flag.kind for flag in run.opened],
                cleared=[flag.kind for flag in run.cleared],
            )
        return run

    async def _contacts(self, project: Project, customer: Customer, since: datetime) -> list[Contact]:
        """The customer reaching out since a moment: inbound events, and conversations they
        started, each counted once."""
        rows = await self.events.reaching_out_since(
            project_id=project.id, customer_id=customer.id, since=since, limit=EVENT_LIMIT
        )
        contacts: list[Contact] = []
        for ident, kind, data, source, at in rows:
            channel = contact_channel(kind, data, source)
            if channel:
                contacts.append(Contact(channel, ensure_utc(at), ident))
        for conversation in await self.sessions.started_since(project_id=project.id, customer_id=customer.id, since=since):
            channel = channel_named(conversation.channel)
            if not channel:
                continue
            started = ensure_utc(conversation.started_at)
            if any(item.channel == channel and abs(item.at - started) <= SAME_CONTACT for item in contacts):
                continue
            contacts.append(Contact(channel, started, conversation.id))
        return contacts

    async def _billing(self, project: Project, customer: Customer, since: datetime) -> list[Billing]:
        rows = await self.events.reaching_out_since(
            project_id=project.id, customer_id=customer.id, since=since, limit=EVENT_LIMIT
        )
        found: list[Billing] = []
        for ident, kind, data, _source, at in rows:
            if not any(marker in kind.lower() for marker in _BILLING):
                continue
            plan = billing_plan(data.get("plan"))
            if plan:
                found.append(Billing(plan, ensure_utc(at), ident))
        return found

    async def _activity(self, project: Project, customer: Customer, since: datetime, now: datetime) -> int:
        return await self.events.count_between(project_id=project.id, customer_id=customer.id, since=since, until=now)

    # ------------------------------------------------------------------ read

    async def list(
        self,
        *,
        project: Project,
        status: str | None = OPEN,
        kind: str | None = None,
        customer: Customer | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Flags as this reader may see them: a flag on a memory they may not read is not
        shown at all. Returns the page, the total they may see, and how many were withheld."""
        if status is not None and status not in STATUSES:
            raise ValidationError(f"'status' is one of {', '.join(STATUSES)}.")
        if kind is not None and kind not in KINDS:
            raise ValidationError(f"'kind' is one of {', '.join(KINDS)}.")
        hidden: frozenset[str] = frozenset()
        if not self.reader.sees_everything:
            candidates, _ = await self.drift.list(
                project_id=project.id, status=status, kind=kind, customer_id=customer.id if customer else None, limit=5000
            )
            hidden = await self.reader.hidden(project.id, {row.memory_id for row in candidates})
        rows, total = await self.drift.list(
            project_id=project.id,
            status=status,
            kind=kind,
            customer_id=customer.id if customer else None,
            exclude_memory_ids=sorted(hidden),
            limit=limit,
            offset=offset,
        )
        withheld = 0
        if hidden:
            _, everything = await self.drift.list(
                project_id=project.id, status=status, kind=kind, customer_id=customer.id if customer else None, limit=1
            )
            withheld = everything - total
        return await self._out(project, rows), total, withheld

    async def get(self, *, project: Project, drift_id: str) -> dict[str, Any]:
        flag = await self._visible(project, drift_id)
        return (await self._out(project, [flag]))[0]

    async def _visible(self, project: Project, drift_id: str, *, for_update: bool = False) -> MemoryDrift:
        flag = await self.drift.get(drift_id, project.id, for_update=for_update)
        if flag is None or flag.memory_id in await self.reader.hidden(project.id, [flag.memory_id]):
            raise NotFoundError("Drift flag not found.")
        return flag

    async def _out(self, project: Project, rows: list[MemoryDrift]) -> list[dict[str, Any]]:
        memories = {row.id: row for row in await self.memories.get_many([flag.memory_id for flag in rows], project.id)}
        customers = {
            row.id: row
            for row in await self.customers.get_many([flag.customer_id for flag in rows], project.id)
        } if rows else {}
        return [drift_out(flag, memories.get(flag.memory_id), customers.get(flag.customer_id)) for flag in rows]

    # ------------------------------------------------------------------ act

    async def confirm(
        self,
        *,
        project: Project,
        drift_id: str,
        note: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """The evidence is right: write the change it points to, through the normal paths."""
        flag = await self._visible(project, drift_id, for_update=True)
        if flag.status != OPEN:
            raise ConflictError(f"This flag is already {flag.status}.")
        memory = await self.memories.get(flag.memory_id, project.id)
        customer = await self.customers.get(flag.customer_id, project.id)
        if memory is None or customer is None or str(memory.status) != MemoryStatus.ACTIVE.value:
            await self.drift.resolve(flag, status=CLEARED, by=None, by_type="system", note="The memory it was about is no longer standing.")
            raise ConflictError("The memory this flag was about is no longer standing; the flag was cleared.")

        replacement = await self._apply(project, customer, memory, flag, note=note)
        await self.drift.resolve(
            flag,
            status=CONFIRMED,
            by=actor_id,
            by_type=actor_type,
            note=note,
            replacement_memory_id=replacement.id if replacement is not None else None,
        )
        await self._audit(project, flag, "confirmed", actor_type=actor_type, actor_id=actor_id, note=note)
        dispatcher = WebhookDispatcher(self.session)
        await dispatcher.emit(memory_drift_resolved(project_id=project.id, customer=customer, drift=flag))
        if replacement is not None:
            await dispatcher.emit(memory_created(project_id=project.id, memory=replacement, customer=customer))
            if str(memory.status) == MemoryStatus.SUPERSEDED.value:
                await dispatcher.emit(
                    memory_conflict(
                        project_id=project.id,
                        memory=replacement,
                        superseded_memory_id=memory.id,
                        reason=f"A person confirmed drift: {flag.summary}",
                    )
                )
        # What the customer is — lifecycle, snapshot — follows at once, not at the next event.
        from app.services.customer_state_service import CustomerStateService

        await CustomerStateService(self.session).refresh(project=project, customer=customer, reason="drift_confirmed")
        logger.info("memory.drift_confirmed", drift_id=flag.id, kind=flag.kind, replacement=replacement.id if replacement else None)
        return (await self._out(project, [flag]))[0]

    async def dismiss(
        self,
        *,
        project: Project,
        drift_id: str,
        note: str | None = None,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """The memory is still right: keep it, and count only evidence from now on."""
        flag = await self._visible(project, drift_id, for_update=True)
        if flag.status != OPEN:
            raise ConflictError(f"This flag is already {flag.status}.")
        await self.drift.resolve(flag, status=DISMISSED, by=actor_id, by_type=actor_type, note=note)
        await self._audit(project, flag, "dismissed", actor_type=actor_type, actor_id=actor_id, note=note)
        customer = await self.customers.get(flag.customer_id, project.id)
        await WebhookDispatcher(self.session).emit(memory_drift_resolved(project_id=project.id, customer=customer, drift=flag))
        return (await self._out(project, [flag]))[0]

    async def settle_for_memory(
        self,
        *,
        project: Project,
        memory: Memory,
        status: str,
        note: str,
        actor_type: str = "user",
        actor_id: str | None = None,
    ) -> list[MemoryDrift]:
        """Close a memory's open flags because a person acted on the memory itself: confirming
        it dismisses them; rejecting or correcting it clears them."""
        flags = (await self.drift.open_for_memories(project.id, [memory.id])).get(memory.id, [])
        for flag in flags:
            await self.drift.resolve(flag, status=status, by=actor_id, by_type=actor_type, note=note)
        return flags

    async def _apply(
        self, project: Project, customer: Customer, memory: Memory, flag: MemoryDrift, *, note: str | None
    ) -> Memory | None:
        confirms = {"confirms_drift": flag.id, "evidence": list(flag.evidence or [])[:10], "feedback_note": note}
        if flag.kind == "channel":
            observed = display_channel(flag.observed) or str(flag.observed)
            content = f"The customer prefers {observed}."
            return await self._write(
                project, customer, MemoryType.PREFERENCE, content, memory,
                meta={**confirms, "channels": [observed]}, supersede=True,
            )
        if flag.kind == "plan":
            stated, observed = str(flag.stated), str(flag.observed)
            direction = plan_direction(stated, observed)
            verb = {"upgraded": "upgraded from", "downgraded": "downgraded from"}.get(direction)
            content = (
                f"The customer {verb} the {stated.title()} plan to the {observed.title()} plan."
                if verb
                else f"The customer is on the {observed.title()} plan."
            )
            # A state statement ("is on Pro") is replaced; a transition is history and stays.
            return await self._write(
                project, customer, MemoryType.SUBSCRIPTION, content, memory,
                meta={**confirms, "plan": observed, "previous_plan": stated, "direction": direction},
                supersede=not is_transition(memory.content),
            )
        if flag.kind == "usage":
            feature = str(flag.stated)
            last = (flag.counts or {}).get("quiet_days")
            quiet = f" for {span(int(last))}" if isinstance(last, (int, float)) else ""
            content = f"The customer has not used the {feature} feature{quiet}."
            return await self._write(
                project, customer, MemoryType.BEHAVIOR, content, memory,
                meta={**confirms, "feature": feature, "stopped": True}, supersede=True,
            )
        if flag.kind == "quiet_problem":
            quiet = (flag.counts or {}).get("quiet_days")
            since = f" (not reported since {day(flag.since)})" if flag.since else ""
            words = " ".join(memory.content.split()).rstrip(".")
            content = f"The problem “{words}” is resolved{since}."
            return await self._write(
                project, customer, MemoryType.FACT, content, memory,
                meta={**confirms, "resolved": True, "supersedes": memory.id, "quiet_days": quiet}, supersede=True,
            )
        raise ValidationError(f"Unknown drift kind '{flag.kind}'.")  # pragma: no cover

    async def _write(
        self,
        project: Project,
        customer: Customer,
        kind: MemoryType,
        content: str,
        replaces: Memory,
        *,
        meta: dict[str, Any],
        supersede: bool,
    ) -> Memory:
        # New words are classified afresh, like a correction: they can carry a restricted term.
        sensitivity, restricted_by = classify(project.settings, content=content, memory_type=kind.value)
        now = utcnow()
        memory = await self.memories.create(
            project_id=project.id,
            customer_id=customer.id,
            type=kind,
            content=content,
            importance=max(0.5, float(replaces.importance)),
            confidence=0.95,  # a person confirmed it from the evidence
            source=MemorySource.MANUAL,
            source_event_ids=[ident for ident in meta.get("evidence", []) if str(ident).startswith("evt_")],
            first_seen_at=now,
            last_seen_at=now,
            sensitivity=Sensitivity(sensitivity),
            metadata={**meta, "confirmed_at": now.isoformat(), **({"restricted_by": restricted_by} if restricted_by else {})},
        )
        if supersede:
            await self.memories.supersede(replaces, superseded_by=memory.id, reason="drift_confirmed")
        if self.embedder is not None:
            await self.memories.embed(memory, self.embedder)
        return memory

    async def _audit(
        self, project: Project, flag: MemoryDrift, verdict: str, *, actor_type: str, actor_id: str | None, note: str | None
    ) -> None:
        await AuditRepository(self.session).record(
            action=AuditAction.MEMORY_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="memory_drift",
            resource_id=flag.id,
            metadata={
                "verdict": verdict,
                "kind": flag.kind,
                "memory_id": flag.memory_id,
                "summary": flag.summary,
                "note": note,
                "replacement_memory_id": flag.replacement_memory_id,
            },
        )


# ---------------------------------------------------------------------- helpers


def feature_of(memory: Any) -> str | None:
    """The feature a behaviour memory is about: stored by the template, or read from its
    words ("The customer uses the Campaign Builder feature.") for memories written before."""
    stored = (getattr(memory, "meta", None) or {}).get("feature")
    if stored:
        return str(stored)
    match = _USES_FEATURE.search(memory.content or "")
    return match.group(1).strip() if match else None


def billing_plan(value: Any) -> str | None:
    """The plan a billing event names, lowercase: "Enterprise (annual)", "enterprise_monthly"
    and "Enterprise" are all "enterprise"; an unknown name is kept as written."""
    if not isinstance(value, str) or not value.strip():
        return None
    words = value.replace("_", " ").replace("-", " ")
    named = plans_named(words)
    if named:
        return named[-1]
    return " ".join(words.lower().split()) or None


def plan_direction(stated: str, observed: str) -> str:
    """upgraded or downgraded when both plans are on the known ladder; else changed."""
    try:
        before, after = _PLAN_ORDER.index(stated.lower()), _PLAN_ORDER.index(observed.lower())
    except ValueError:
        return "changed"
    return "upgraded" if after > before else "downgraded" if after < before else "changed"


def drift_out(flag: MemoryDrift, memory: Memory | None, customer: Customer | None) -> dict[str, Any]:
    return {
        "id": flag.id,
        "kind": flag.kind,
        "kind_label": KIND_LABELS.get(flag.kind, flag.kind),
        "status": flag.status,
        "stated": flag.stated,
        "observed": flag.observed,
        "summary": flag.summary,
        "counts": flag.counts or {},
        "evidence": list(flag.evidence or []),
        "since": flag.since,
        "detected_at": flag.detected_at,
        "updated_at": flag.updated_at,
        "resolved_at": flag.resolved_at,
        "resolved_by_type": flag.resolved_by_type,
        "note": flag.note,
        "replacement_memory_id": flag.replacement_memory_id,
        "memory": (
            {
                "id": memory.id,
                "type": str(memory.type),
                "content": memory.content,
                "status": str(memory.status),
                "last_seen_at": memory.last_seen_at,
            }
            if memory is not None
            else {"id": flag.memory_id}
        ),
        "customer": (
            {"id": customer.id, "external_id": customer.external_id, "name": customer.name}
            if customer is not None
            else {"id": flag.customer_id}
        ),
    }
