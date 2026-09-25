"""A customer's journey as milestones, not rows (§26 6.6).

"Don't simply expose every event." A customer's history is a handful of moments that
changed something — they started using Shopify, the first Shopify failure, the third report
of it, a goal set, health crossing a band, a downgrade, the lifecycle moving to at risk — and
this module finds them in records that already exist, each read for what it is
authoritative on (the inputs "what changed" reads, §26 4.1, over the whole history):

* **events** — where the history begins, the first use of each feature and integration, and
  long silences and the returns that end them;
* **memories** — problems first reported, reported again past a threshold, and resolved;
  plan changes; intents; opt-outs and changed preferences; strong feedback; relationships;
* **goal evidence** — goals set, achieved, stalled and abandoned;
* **snapshots** — health crossing a band, or falling or rising sharply within one;
* **lifecycle stays** — every track's moves, with the reasons in words.

A milestone answers the five questions a person expands it for: what happened, why it
mattered, which memories changed, what happened to health, and which state transition
followed. The last three come from a chain the pipeline records as it goes — the event
behind a memory, the snapshot that event's refresh took, the stays entered in the same
refresh — so they are read, not guessed from timing.

A milestone is placed at the time of the event behind it. Health and lifecycle are judged
when an event is processed; for history imported after the fact that is later than the
event, so each milestone also carries ``recorded_at``, and the event decides where it sits.

Everything here is pure: the service gathers rows; :func:`detect` finds the milestones;
:func:`shape` applies one reader's view; :func:`select`, :func:`summarise` and
:func:`markdown` present them.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from common.time import ensure_utc
from memory_engine.changes import subscription_change
from memory_engine.consolidation.rules import without_recurrence_note
from memory_engine.facts import DAYS_SUFFIX, LIFECYCLE_PREFIX, polarity
from memory_engine.policy import WITHHELD
from memory_engine.reasons import reasons as reasons_in_words
from memory_engine.reasons import sentence
from memory_engine.topics import topics_of
from nlp.entities import channel_stance, display_channel
from nlp.intents import intent_kinds
from nlp.optouts import opt_outs
from nlp.sentiment import feedback_band, feedback_strength, feedback_tone

CATEGORIES = (
    "account", "usage", "problem", "plan", "intent", "preference", "goal", "health",
    "lifecycle", "activity", "feedback", "relationship",
)

# How much a milestone matters, for choosing the ones worth showing and for the summary.
_IMPORTANCE: dict[tuple[str, str], float] = {
    ("plan", "cancelled"): 1.0,
    ("plan", "downgraded"): 0.95,
    ("intent", "churn"): 0.9,
    ("plan", "upgraded"): 0.9,
    ("problem", "first"): 0.85,
    ("health", "dropped_critical"): 0.9,
    ("health", "dropped_at_risk"): 0.85,
    ("health", "dropped_watch"): 0.75,
    ("health", "swing"): 0.7,
    ("lifecycle", "moved"): 0.85,
    ("goal", "achieved"): 0.85,
    ("problem", "repeated"): 0.8,
    ("plan", "started"): 0.8,
    ("plan", "changed"): 0.8,
    ("health", "recovered"): 0.75,
    ("problem", "resolved"): 0.75,
    ("activity", "quiet"): 0.75,
    ("intent", "growth"): 0.75,
    ("account", "started"): 0.7,
    ("problem", "new"): 0.7,
    ("plan", "renewed"): 0.7,
    ("goal", "set"): 0.7,
    ("goal", "stalled"): 0.7,
    ("goal", "abandoned"): 0.7,
    ("preference", "opted_out"): 0.7,
    ("feedback", "negative"): 0.7,
    ("activity", "returned"): 0.7,
    ("preference", "changed"): 0.65,
    ("preference", "stated"): 0.5,
    ("usage", "first_use"): 0.6,
    ("intent", "other"): 0.6,
    ("feedback", "positive"): 0.6,
    ("relationship", "changed"): 0.55,
    ("relationship", "added"): 0.5,
}

# A problem's reports worth a milestone: the third is where escalating is recommended.
REPORT_THRESHOLDS = (3, 5, 10)
# A fall or rise within one band worth a milestone of its own.
HEALTH_SWING = 15.0
# A band crossed and crossed back this soon is one wobble, not two milestones.
FLAP = timedelta(days=2)
# No events for this long is a silence.
QUIET_DAYS = 30
# Strong enough feedback to quote.
STRONG_FEEDBACK = 0.5
# The stays one refresh enters are written within milliseconds of its snapshot.
LINK_SECONDS = 5.0
# Automatic steps within a second of a track's first placement are part of placing them.
PLACEMENT_SECONDS = 1.0
# ``recorded_at`` is worth saying when it is this far from when it happened.
RECORDED_LATE = timedelta(days=1)
DRIVERS = 4

_CHURN_INTENTS = frozenset({"cancellation", "downgrade"})
_GROWTH_INTENTS = frozenset({"expansion", "renewal", "purchase"})
_INTENT_TITLES = {
    "cancellation": "Said they may cancel",
    "downgrade": "Talked about downgrading",
    "expansion": "Talked about expanding",
    "renewal": "Talked about renewing",
    "purchase": "Asked about buying",
    "evaluation": "Said they are evaluating options",
    "migration": "Planning a migration",
    "integration": "Planning an integration",
}
_OPT_OUT_TITLES = {
    "contact": "Asked not to be contacted",
    "phone": "Asked not to be called",
    "email": "Asked not to be emailed",
    "sms": "Asked not to be texted",
    "whatsapp": "Asked not to be messaged on WhatsApp",
    "sales": "Asked for no sales outreach",
    "marketing": "Opted out of marketing",
}
_BANDS = ("critical", "at_risk", "watch", "healthy")
_NEGATIVE_STATES = frozenset({"at_risk", "churned", "critical", "inactive", "dormant", "lost", "cancelled"})
_POSITIVE_STATES = frozenset(
    {"active", "expanding", "power_user", "adopting", "activated", "paying", "renewing", "won_back"}
)


@dataclass(slots=True)
class Milestone:
    category: str
    kind: str
    title: str
    at: datetime
    what: str
    why: str | None = None
    tone: str = "neutral"  # positive | negative | neutral
    topics: list[str] = field(default_factory=list)
    # The record that must be visible for the milestone to be shown at all, and one whose
    # words ``what`` also quotes — with ``what_masked`` to show a reader who may not see it.
    subject: str | None = None
    before_id: str | None = None
    what_masked: str | None = None
    # The event behind it: the start of the chain to health and the lifecycle.
    event_id: str | None = None
    # A snapshot or stay that *is* the milestone.
    point_id: str | None = None
    recorded_at: datetime | None = None
    memories: list[dict[str, Any]] = field(default_factory=list)
    health: dict[str, Any] | None = None
    transitions: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0

    @property
    def importance(self) -> float:
        return round(_IMPORTANCE.get((self.category, self.detail.get("rank", self.kind)), 0.5) * self.weight, 3)

    @property
    def id(self) -> str:
        """Stable across reads: the same moment is the same milestone tomorrow."""
        key = f"{self.category}:{self.kind}:{self.subject or self.point_id or ''}:{ensure_utc(self.at).isoformat()}:{self.detail.get('key', '')}"
        return "jm_" + hashlib.sha1(key.encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        late = self.recorded_at is not None and abs(ensure_utc(self.recorded_at) - ensure_utc(self.at)) >= RECORDED_LATE
        return {
            "id": self.id,
            "at": self.at,
            "recorded_at": self.recorded_at if late else None,
            "category": self.category,
            "kind": self.kind,
            "title": self.title,
            "tone": self.tone,
            "importance": self.importance,
            "topics": list(self.topics),
            "what_happened": self.what,
            "why_it_matters": self.why,
            "memories": list(self.memories),
            "health": self.health,
            "transitions": list(self.transitions),
            "evidence": {name: list(ids) for name, ids in self.evidence.items()},
            "detail": {name: value for name, value in self.detail.items() if name not in ("rank", "key")},
        }


# ------------------------------------------------------------------ inputs


@dataclass(slots=True)
class Point:
    """A snapshot, as the reader may see it."""

    id: str
    taken_at: datetime
    event_id: str | None
    values: dict[str, Any]
    changes: list[dict[str, Any]]
    # When the event behind it happened; ``taken_at`` when there is none.
    at: datetime | None = None


@dataclass(slots=True)
class Stay:
    """A lifecycle stay, with its evaluation as the reader may see it."""

    row: Any
    evaluation: dict[str, Any]


@dataclass(slots=True)
class FirstUse:
    kind: str  # feature | integration
    name: str
    first_at: datetime
    first_event_id: str
    last_at: datetime
    count: int
    event_type: str = ""


@dataclass(slots=True)
class Gap:
    last_at: datetime
    last_event_id: str | None
    returned_at: datetime | None  # None: still quiet
    returned_event_id: str | None = None
    returned_type: str | None = None


@dataclass(slots=True)
class JourneyInputs:
    now: datetime
    customer_name: str = ""
    customer_since: datetime | None = None
    # (id, type, occurred_at) of their first event.
    first_event: tuple[str, str, datetime] | None = None
    # Every memory their events produced, any status but deleted, oldest first.
    memories: Sequence[Any] = ()
    # Memories those refer to — what they superseded, what superseded them — by id.
    related: dict[str, Any] = field(default_factory=dict)
    # (version, memory) for reports that repeated a problem, oldest first.
    versions: Sequence[tuple[Any, Any]] = ()
    goals: Sequence[Any] = ()
    stays: Sequence[Stay] = ()
    points: Sequence[Point] = ()
    # event id -> (occurred_at, event type), for the events the chain passes through.
    events: dict[str, tuple[datetime, str]] = field(default_factory=dict)
    first_uses: Sequence[FirstUse] = ()
    gaps: Sequence[Gap] = ()
    track_labels: dict[str, str] = field(default_factory=dict)
    # The project's health weights (defaults merged), for saying what a moment did to health.
    weights: dict[str, float] = field(default_factory=dict)
    # Normalised entity name → its type in the graph, so only products, integrations and
    # features are topics (:mod:`memory_engine.topics`).
    topic_types: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers


def _meta(memory: Any) -> dict[str, Any]:
    meta = getattr(memory, "meta", None)
    return meta if isinstance(meta, dict) else {}


def _words(value: Any) -> str:
    return str(value or "").replace("_", " ")


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() or len(text) == 1 else text


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y}"


def _span(delta: timedelta) -> str:
    """"3 days", "5 weeks", "4 months" — how long, in the unit a person would use."""
    days = max(0.0, delta.total_seconds() / 86400)
    if days < 1:
        return "less than a day"
    if days < 14:
        whole = round(days)
        return f"{whole} day{'s' if whole != 1 else ''}"
    if days < 60:
        weeks = round(days / 7)
        return f"{weeks} weeks"
    months = round(days / 30)
    if months < 24:
        return f"{months} months"
    return f"{round(days / 365)} years"


def _quote(text: str | None) -> str:
    """A memory's words, as said — without the recurrence note consolidation appends, since
    a milestone gives the count itself."""
    words = without_recurrence_note(" ".join(str(text or "").split()))
    return f"“{words.rstrip('.')}”"


def _ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


_GOAL_FRAME = re.compile(
    r"^(?:the customer(?:'s)? (?:goal|objective|aim|plan) is (?:to )?"
    r"|the customer (?:wants|plans|aims|hopes|intends|needs|would like|is trying|is looking|is planning) to "
    r"|(?:our|their) (?:goal|objective|aim) is (?:to )?"
    r"|we (?:want|plan|aim|hope|need|would like|are trying|are looking|are planning) to )",
    re.IGNORECASE,
)


def goal_words(statement: str) -> str:
    """A goal as a person names it: "The customer's goal is launch automation." → "Launch
    automation"."""
    text = " ".join(str(statement or "").split()).rstrip(".")
    stripped = _GOAL_FRAME.sub("", text).strip()
    return _upper_first(stripped) if stripped else text


def _state_words(state: Any) -> str:
    return _words(state)


def _topic(memory: Any, inputs: JourneyInputs) -> str | None:
    """The first product, feature or integration a memory names — not the customer."""
    found = topics_of(memory, inputs.topic_types, exclude=inputs.customer_name, limit=1)
    return found[0] if found else None


def _event_at(inputs: JourneyInputs, event_id: str | None, fallback: datetime) -> datetime:
    known = inputs.events.get(event_id or "")
    return ensure_utc(known[0]) if known else ensure_utc(fallback)


def _first_event(memory: Any) -> str | None:
    ids = list(getattr(memory, "source_event_ids", None) or [])
    return str(ids[0]) if ids else None


def _memory_ref(memory: Any, change: str) -> dict[str, Any]:
    return {"id": memory.id, "type": str(memory.type), "content": memory.content, "change": change}


def _hurts(inputs: JourneyInputs, key: str) -> bool:
    return float(inputs.weights.get(key, 0.0) or 0.0) < 0


def _helps(inputs: JourneyInputs, key: str) -> bool:
    return float(inputs.weights.get(key, 0.0) or 0.0) > 0


# ------------------------------------------------------------------ detect


def detect(inputs: JourneyInputs) -> list[Milestone]:
    """Every milestone in the customer's history, oldest first, linked to health and the
    lifecycle, before any reader's view is applied."""
    for point in inputs.points:
        point.at = _event_at(inputs, point.event_id, point.taken_at)
    found: list[Milestone] = []
    found.extend(_account(inputs))
    found.extend(_usage(inputs))
    found.extend(_from_memories(inputs))
    found.extend(_repeats(inputs))
    found.extend(_goals(inputs))
    found.extend(_health(inputs))
    found.extend(_lifecycle(inputs))
    found.extend(_activity(inputs))
    _link(found, inputs)
    found.sort(key=_order)
    return found


def _order(milestone: Milestone) -> tuple[datetime, bool, float]:
    """In time; where the history begins first; then the weightier of two at one moment."""
    return (ensure_utc(milestone.at), milestone.category != "account", -milestone.importance)


def _account(inputs: JourneyInputs) -> Iterable[Milestone]:
    if inputs.customer_since is None:
        return
    at = ensure_utc(inputs.customer_since)
    first = inputs.first_event
    if first is not None and ensure_utc(first[2]) <= at:
        yield Milestone(
            "account", "started", "Became a customer", at,
            f"Their first recorded event: {_words(first[1])}.",
            event_id=first[0], evidence={"events": [first[0]]},
        )
    else:
        yield Milestone("account", "started", "Became a customer", at, "Added as a customer.")


def _usage(inputs: JourneyInputs) -> Iterable[Milestone]:
    for use in inputs.first_uses:
        first, last = ensure_utc(use.first_at), ensure_utc(use.last_at)
        what = f"The first recorded use of {'the ' if use.kind == 'feature' else ''}{use.name}"
        what += f" ({_words(use.event_type)})." if use.event_type else "."
        if use.kind == "integration":
            # Connected once and then relied on: a connection is not a use.
            why = (
                f"In use since: {use.count} {use.name} events, the latest on {_day(last)}."
                if use.count > 1
                else "An integration they rely on: when it fails, that is recorded as a problem."
            )
        elif use.count > 1:
            why = f"Used {use.count} times since, most recently on {_day(last)}."
            if _helps(inputs, "feature_adoption"):
                why += " Features in use count towards health."
        else:
            why = "Used once, and not since."
        yield Milestone(
            "usage", "first_use", f"Started using {use.name}", first, what, why, tone="positive",
            topics=[use.name], event_id=use.first_event_id,
            evidence={"events": [use.first_event_id]},
            detail={"kind": use.kind, "uses": use.count, "last_used_at": last, "key": use.name.lower()},
        )


def _problem_status(memory: Any, inputs: JourneyInputs) -> str:
    """What became of a problem, from where it stands now: "is still open after 3 weeks"."""
    opened = ensure_utc(memory.first_seen_at)
    status = str(memory.status)
    replacement = inputs.related.get(str(getattr(memory, "superseded_by", None) or ""))
    if status == "active" and not _meta(memory).get("resolved"):
        expires = getattr(memory, "expires_at", None)
        if expires is not None and ensure_utc(expires) <= inputs.now:
            return f"lapsed on {_day(ensure_utc(expires))} without being resolved"
        return f"is still open after {_span(inputs.now - opened)}"
    if replacement is not None and _meta(replacement).get("resolved"):
        closed = ensure_utc(replacement.first_seen_at)
        return f"was resolved on {_day(closed)}, after {_span(closed - opened)}"
    if status == "expired":
        return "has since expired"
    if status == "superseded":
        return "was later replaced by a newer statement"
    return "is no longer standing"


def _from_memories(inputs: JourneyInputs) -> Iterable[Milestone]:
    topics_seen: set[str] = set()
    any_problem = False
    previous_subscription: Any | None = None
    for memory in sorted(inputs.memories, key=lambda item: ensure_utc(item.first_seen_at)):
        kind = str(memory.type)
        meta = _meta(memory)
        if meta.get("human_rejected"):
            continue
        at = ensure_utc(memory.first_seen_at)
        replaced = inputs.related.get(str(meta.get("supersedes") or ""))
        topic = _topic(memory, inputs)
        base: dict[str, Any] = {
            "subject": memory.id,
            "event_id": _first_event(memory),
            "recorded_at": getattr(memory, "created_at", None),
            "topics": [topic] if topic else [],
        }
        refs = [_memory_ref(memory, "created")]
        if replaced is not None:
            refs.append(_memory_ref(replaced, "superseded"))

        # A resolution is typed a fact by the extractor; it resolves the problem it superseded.
        if meta.get("resolved") and (kind == "problem" or (replaced is not None and str(replaced.type) == "problem")):
            topic = topic or (_topic(replaced, inputs) if replaced is not None else None)
            what = f"{_quote(memory.content)}."
            if replaced is not None:
                what += f" It had been open for {_span(at - ensure_utc(replaced.first_seen_at))}."
            why = (
                "One fewer open problem: it no longer pulls health down."
                if _hurts(inputs, "open_problem")
                else "One fewer open problem."
            )
            yield Milestone(
                "problem", "resolved", f"{topic} problem resolved" if topic else "Problem resolved", at, what, why,
                tone="positive", memories=refs, before_id=replaced.id if replaced is not None else None,
                what_masked=f"{_quote(memory.content)}.",
                evidence={"memories": [memory.id, *([replaced.id] if replaced is not None else [])]},
                **{**base, "topics": [topic] if topic else []},
            )
            continue

        if kind == "problem":
            first_of_topic = topic is not None and topic.lower() not in topics_seen
            if first_of_topic:
                title, rank = f"First {topic} problem", "first"
            elif not any_problem:
                title, rank = "First problem", "first"
            else:
                title, rank = (f"New {topic} problem" if topic else "New problem"), "new"
            if topic:
                topics_seen.add(topic.lower())
            any_problem = True
            weight = 1.0 if topic or rank == "new" else 0.95
            status = _problem_status(memory, inputs)
            why = (
                f"Open problems pull health down until they are resolved; this one {status}."
                if _hurts(inputs, "open_problem")
                else f"It {status}."
            )
            yield Milestone(
                "problem", rank, title, at, f"They reported: {_quote(memory.content)}.", why, tone="negative",
                memories=refs, evidence={"memories": [memory.id]}, weight=weight,
                detail={"reports": int(getattr(memory, "evidence_count", 1) or 1), "rank": rank}, **base,
            )
        elif kind == "subscription":
            prior = replaced if replaced is not None and str(replaced.type) == "subscription" else previous_subscription
            change = subscription_change(memory, prior, at)
            previous_subscription = memory
            direction = str(change.detail.get("direction") or "")
            rank = {
                "cancelled": "cancelled", "downgraded": "downgraded", "upgraded": "upgraded",
                "renewed": "renewed", "started": "started",
            }.get(direction, "changed")
            if rank == "downgraded":
                why = (
                    "Downgrades count heavily against health, and the forecast reads them as churn language."
                    if _hurts(inputs, "downgrade")
                    else "Less revenue from them than before."
                )
                tone = "negative"
            elif rank == "cancelled":
                why, tone = "The subscription ended.", "negative"
            elif rank == "upgraded":
                why = "An upgrade counts towards health." if _helps(inputs, "upgrade") else "More revenue from them."
                tone = "positive"
            elif rank == "renewed":
                why, tone = "They chose to stay.", "positive"
            elif rank == "started":
                why, tone = "Where their paid relationship begins.", "neutral"
            else:
                why, tone = None, "neutral"
            what = f"{_quote(memory.content)}."
            yield Milestone(
                "plan", rank, change.title, at, what, why, tone=tone,
                memories=[_memory_ref(memory, "created"), *([_memory_ref(prior, "superseded")] if prior is not None and prior is replaced else [])],
                evidence={"memories": list(change.evidence)},
                before_id=change.before_id,
                detail=dict(change.detail),
                **{**base, "topics": []},
            )
        elif kind == "intent":
            kinds = intent_kinds(memory.content)
            lead = kinds[0] if kinds else None
            if lead in _CHURN_INTENTS:
                rank, tone = "churn", "negative"
                why = (
                    "Churn language pulls health down and raises the forecast's churn risk."
                    if _hurts(inputs, "churn_language")
                    else "They said it: the strongest sign a customer gives that they may leave."
                )
            elif lead in _GROWTH_INTENTS:
                rank, tone, why = "growth", "positive", "An opening to grow the account."
            else:
                rank, tone, why = "other", "neutral", None
            title = _INTENT_TITLES.get(lead or "", "Stated an intent")
            yield Milestone(
                "intent", "expressed", title, at, f"They said: {_quote(memory.content)}.", why, tone=tone,
                memories=refs, evidence={"memories": [memory.id]}, detail={"kinds": kinds, "rank": rank}, **base,
            )
        elif kind in ("preference", "feedback") and opt_outs(memory.content):
            kinds = opt_outs(memory.content)
            yield Milestone(
                "preference", "opted_out", _OPT_OUT_TITLES.get(kinds[0], "Opted out"), at,
                f"They said: {_quote(memory.content)}.",
                "Guardrails refuse the outreach it rules out, for every agent.",
                memories=refs, evidence={"memories": [memory.id]}, detail={"kinds": kinds}, **base,
            )
        elif kind == "preference" and replaced is None and channel_stance(memory.content)[0]:
            channel = display_channel(channel_stance(memory.content)[0][0])
            yield Milestone(
                "preference", "stated", f"Prefers {channel}", at, f"They said: {_quote(memory.content)}.",
                f"Agents and guardrails follow it: contact goes through {channel}.",
                memories=refs, evidence={"memories": [memory.id]}, detail={"channel": channel}, **base,
            )
        elif kind == "preference" and replaced is not None and str(replaced.type) == "preference":
            wanted_now, _ = channel_stance(memory.content)
            wanted_before, _ = channel_stance(replaced.content)
            new = str(wanted_now[0]).lower() if wanted_now else None
            old = str(wanted_before[0]).lower() if wanted_before else None
            channel = wanted_now[0] if new and new != old else None
            title = f"Preferred channel changed to {display_channel(channel)}" if channel else "Changed a preference"
            by_person = str(getattr(memory, "source", "")) == "manual"
            yield Milestone(
                "preference", "changed", title, at,
                f"Now: {_quote(memory.content)} (was: {_quote(replaced.content)}).",
                "Agents and guardrails follow the newest preference." + (" A person confirmed it." if by_person else ""),
                memories=refs, before_id=replaced.id, what_masked=f"Now: {_quote(memory.content)}.",
                evidence={"memories": [memory.id, replaced.id]}, detail={"by_person": by_person}, **base,
            )
        elif (
            kind == "feedback"
            and feedback_strength(memory.content, meta) >= STRONG_FEEDBACK
            and feedback_tone(memory.content, meta) != "neutral"
        ):
            negative = feedback_tone(memory.content, meta) == "negative"
            band = feedback_band(memory.content, meta)
            if negative:
                why = "Negative feedback counts against health." if _hurts(inputs, "negative_feedback") else None
            else:
                why = "Positive feedback supports health." if _helps(inputs, "positive_feedback") else None
            yield Milestone(
                "feedback", "negative" if negative else "positive",
                "Negative feedback" if negative else "Praise", at, f"{_quote(memory.content)}.", why,
                tone="negative" if negative else "positive", memories=refs, evidence={"memories": [memory.id]},
                detail={"polarity": round(polarity(memory), 2), **({"band": band} if band else {})}, **base,
            )
        elif kind == "relationship":
            changed = replaced is not None and str(replaced.type) == "relationship"
            yield Milestone(
                "relationship", "changed" if changed else "added",
                "A relationship changed" if changed else "New relationship", at, f"{_quote(memory.content)}.",
                "Who to talk to, and who decides.", memories=refs, before_id=replaced.id if changed else None,
                evidence={"memories": [memory.id, *([replaced.id] if changed else [])]}, **base,
            )


def _repeats(inputs: JourneyInputs) -> Iterable[Milestone]:
    """A problem reported the 3rd, 5th, 10th time — counted from its first report."""
    counted: dict[str, int] = {}
    for version, memory in sorted(inputs.versions, key=lambda pair: ensure_utc(pair[0].created_at)):
        if str(memory.type) != "problem" or _meta(memory).get("resolved"):
            continue
        reports = counted.get(memory.id, 1) + 1
        counted[memory.id] = reports
        if reports not in REPORT_THRESHOLDS or reports > int(getattr(memory, "evidence_count", reports) or reports):
            continue
        event_id = getattr(version, "source_event_id", None)
        at = _event_at(inputs, event_id, version.created_at)
        topic = _topic(memory, inputs)
        title = f"{topic} problem reported {reports} times" if topic else f"Problem reported {reports} times"
        if reports == REPORT_THRESHOLDS[0]:
            why = "Three reports without a resolution is where escalating is recommended: first-line support has not been enough."
        else:
            why = f"{reports} reports, and still not resolved."
        yield Milestone(
            "problem", "repeated", title, at, f"The {_ordinal(reports)} report of {_quote(memory.content)}.", why,
            tone="negative", topics=[topic] if topic else [], subject=memory.id, event_id=event_id,
            recorded_at=version.created_at, memories=[_memory_ref(memory, "reported again")],
            evidence={"memories": [memory.id]}, detail={"reports": reports, "key": str(reports)},
        )


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _goals(inputs: JourneyInputs) -> Iterable[Milestone]:
    by_id = {memory.id: memory for memory in inputs.memories}
    for goal in inputs.goals:
        statement = goal_words(str(goal.statement or ""))
        entries = [entry for entry in getattr(goal, "evidence", None) or [] if isinstance(entry, dict)]
        seen: set[str] = set()
        stated = [entry for entry in entries if entry.get("kind") == "stated"]
        if not stated:
            source = by_id.get(str(getattr(goal, "memory_id", None) or ""))
            stated = [{"kind": "stated", "at": source.first_seen_at if source is not None else goal.opened_at,
                       "memory_id": source.id if source is not None else None}]
        for entry in [*stated, *(entry for entry in entries if entry.get("kind") != "stated")]:
            kind = str(entry.get("kind") or "")
            if kind not in ("stated", "achieved", "stalled", "abandoned") or kind in seen:
                continue
            seen.add(kind)  # one milestone per kind per goal: the first time it happened
            memory = by_id.get(str(entry.get("memory_id") or ""))
            at = ensure_utc(memory.first_seen_at) if memory is not None and kind == "stated" else (
                _parse(entry.get("at")) or ensure_utc(goal.last_signal_at)
            )
            refs = [_memory_ref(memory, "created" if kind == "stated" else "recorded against the goal")] if memory is not None else []
            if kind == "stated":
                said = f"They said: {_quote(memory.content)}." if memory is not None else f"{_quote(statement)}."
                milestone = Milestone(
                    "goal", "set", f"Goal set: {_quote(statement)}", at, said,
                    "What they want to achieve; progress is tracked against it.",
                )
            elif kind == "achieved":
                milestone = Milestone(
                    "goal", "achieved", f"Goal achieved: {_quote(statement)}", at,
                    f"Recorded as achieved{f' — {_quote(memory.content)}' if memory is not None else ''}.",
                    "A goal they set, reached — worth acknowledging.", tone="positive",
                )
            elif kind == "stalled":
                idle = entry.get("idle_days")
                milestone = Milestone(
                    "goal", "stalled", f"Goal stalled: {_quote(statement)}", at,
                    f"No progress in {_span(timedelta(days=float(idle)))}." if isinstance(idle, (int, float)) else "No progress for a long time.",
                    "A goal going nowhere is a risk to their success — ask how it is going.", tone="negative",
                )
            else:
                milestone = Milestone(
                    "goal", "abandoned", f"Goal abandoned: {_quote(statement)}", at,
                    f"Recorded as abandoned{f' — {_quote(memory.content)}' if memory is not None else ''}.",
                    "Something they wanted is off the table — worth knowing why.", tone="negative",
                )
            milestone.subject = goal.id
            milestone.memories = refs
            milestone.event_id = _first_event(memory) if memory is not None else None
            milestone.evidence = {"goals": [goal.id], **({"memories": [memory.id]} if memory is not None else {})}
            milestone.detail = {"goal_id": goal.id, "status": str(getattr(goal, "status", "") or "")}
            yield milestone


def _band_index(band: Any) -> int | None:
    try:
        return _BANDS.index(str(band))
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _health(inputs: JourneyInputs) -> Iterable[Milestone]:
    """Health crossing a band between one snapshot and the next — or moving sharply within
    one — with a crossing that is crossed back within :data:`FLAP` left out as a wobble."""
    points = sorted(inputs.points, key=lambda point: ensure_utc(point.taken_at))
    moves: list[tuple[Point, Point, str]] = []
    for before, after in zip(points, points[1:], strict=False):
        band_before, band_after = before.values.get("health.band"), after.values.get("health.band")
        score_before = _number(before.values.get("health.score"))
        score_after = _number(after.values.get("health.score"))
        if WITHHELD in (band_before, band_after) or score_before is None or score_after is None:
            continue
        low, high = _band_index(band_before), _band_index(band_after)
        if low is not None and high is not None and low != high:
            moves.append((before, after, "dropped" if high < low else "recovered"))
        elif abs(score_after - score_before) >= HEALTH_SWING:
            moves.append((before, after, "dropped" if score_after < score_before else "recovered"))

    kept: list[tuple[Point, Point, str]] = []
    skip: set[int] = set()
    for index, (before, after, direction) in enumerate(moves):
        if index in skip:
            continue
        following = moves[index + 1] if index + 1 < len(moves) else None
        if (
            following is not None
            and following[2] != direction
            and following[1].values.get("health.band") == before.values.get("health.band")
            and ensure_utc(following[1].taken_at) - ensure_utc(after.taken_at) <= FLAP
        ):
            skip.add(index + 1)
            continue
        kept.append((before, after, direction))

    for before, after, direction in kept:
        score_before = float(before.values["health.score"])
        score_after = float(after.values["health.score"])
        band_before, band_after = before.values.get("health.band"), after.values.get("health.band")
        crossed = band_before != band_after
        verb = "dropped" if direction == "dropped" else "recovered" if crossed else "rose"
        title = f"Health {verb} {score_before:.0f} → {score_after:.0f}"
        what = (
            f"From {_words(band_before)} to {_words(band_after)}."
            if crossed
            else f"Down {score_before - score_after:.0f} points, still {_words(band_after)}."
            if direction == "dropped"
            else f"Up {score_after - score_before:.0f} points, still {_words(band_after)}."
        )
        why = (
            "A band change is what the lifecycle, the health webhook and the brief watch."
            if crossed
            else f"A {'fall' if direction == 'dropped' else 'rise'} this sharp is worth knowing about even within a band."
        )
        yield Milestone(
            "health", direction, title, after.at or after.taken_at, what, why,
            tone="negative" if direction == "dropped" else "positive",
            event_id=after.event_id, point_id=after.id, recorded_at=after.taken_at,
            evidence={"snapshots": [before.id, after.id]},
            detail={"band_before": band_before, "band_after": band_after, "crossed": crossed,
                    "score_before": score_before, "score_after": score_after, "key": after.id,
                    # A fall into at risk or critical outweighs one into watch.
                    "rank": f"dropped_{band_after}" if crossed and direction == "dropped" else direction if crossed else "swing"},
        )


def _state_tone(previous: Any, state: Any) -> str:
    """Into trouble is negative; out of it, or further along, is positive."""
    if str(state) in _NEGATIVE_STATES:
        return "negative"
    if str(state) in _POSITIVE_STATES and (
        str(previous) not in _POSITIVE_STATES or str(state) in ("expanding", "power_user", "renewing")
    ):
        return "positive"
    return "neutral"


def same_refresh(row: Any, other: Any) -> bool:
    """Whether two stays were entered by one refresh: they share its snapshot — or, for
    stays recorded before stays were tied to snapshots, were written within a second."""
    mine, theirs = getattr(row, "snapshot_id", None), getattr(other, "snapshot_id", None)
    if mine and theirs:
        return mine == theirs
    return abs((ensure_utc(row.entered_at) - ensure_utc(other.entered_at)).total_seconds()) <= PLACEMENT_SECONDS


def placements(stays: Sequence[Stay]) -> set[str]:
    """The automatic steps a track took in the refresh that first placed the customer on it
    — part of placing them, which says when tracking began, not what the customer did."""
    firsts = {
        str(getattr(stay.row, "track", None) or "lifecycle"): stay.row for stay in stays if not stay.row.previous_state
    }
    found: set[str] = set()
    for stay in stays:
        row = stay.row
        first = firsts.get(str(getattr(row, "track", None) or "lifecycle"))
        if row.previous_state and str(row.source) != "manual" and first is not None and same_refresh(row, first):
            found.add(row.id)
    return found


def _lifecycle(inputs: JourneyInputs) -> Iterable[Milestone]:
    placing = placements(inputs.stays)
    latest: dict[str, datetime] = {}
    moves: list[tuple[Stay, str]] = []
    for stay in inputs.stays:
        row = stay.row
        track = str(getattr(row, "track", None) or "lifecycle")
        if not row.previous_state or row.id in placing:
            continue
        moves.append((stay, track))
        latest[track] = max(latest.get(track, ensure_utc(row.entered_at)), ensure_utc(row.entered_at))

    for stay, track in moves:
        row = stay.row
        manual = str(row.source) == "manual"
        label = inputs.track_labels.get(track, _words(track).title())
        words = reasons_in_words(stay.evaluation) if stay.evaluation else ([row.reason] if manual and row.reason else [])
        passed = ensure_utc(row.entered_at) < latest[track] and _passed_through(row, moves)
        what = f"From {_state_words(row.previous_state)} to {_state_words(row.state)}" + (" — set by a person." if manual else ".")
        weight = (1.0 if track == "lifecycle" else 0.85) * (0.5 if passed else 1.0)
        yield Milestone(
            "lifecycle", "moved", f"{label} → {_upper_first(_state_words(row.state))}",
            ensure_utc(row.entered_at), what,
            ("Because " + "; ".join(word.rstrip(".") for word in words) + ".") if words else None,
            tone=_state_tone(row.previous_state, row.state),
            point_id=str(getattr(row, "snapshot_id", None) or "") or None,
            recorded_at=row.entered_at,
            transitions=[_transition(stay, inputs)],
            evidence={"states": [row.id], "memories": list(getattr(row, "evidence", None) or [])[:10]},
            detail={"track": track, "from": row.previous_state, "to": row.state, "transition": row.transition,
                    "manual": manual, "passed_through": passed, "key": row.id, "stay_id": row.id},
            weight=weight,
        )


def _passed_through(row: Any, moves: list[tuple[Stay, str]]) -> bool:
    """A state left again within the same refresh — a step on the way to another."""
    track = str(getattr(row, "track", None) or "lifecycle")
    entered = ensure_utc(row.entered_at)
    return any(
        other_track == track
        and other.row is not row
        and ensure_utc(other.row.entered_at) > entered
        and same_refresh(row, other.row)
        for other, other_track in moves
    )


def _transition(stay: Stay, inputs: JourneyInputs) -> dict[str, Any]:
    row = stay.row
    track = str(getattr(row, "track", None) or "lifecycle")
    manual = str(row.source) == "manual"
    words = reasons_in_words(stay.evaluation) if stay.evaluation else ([row.reason] if manual and row.reason else [])
    return {
        "id": row.id,
        "track": track,
        "label": inputs.track_labels.get(track, _words(track).title()),
        "before": row.previous_state,
        "after": row.state,
        "reasons": words,
        "manual": manual,
    }


def _activity(inputs: JourneyInputs) -> Iterable[Milestone]:
    for gap in inputs.gaps:
        last = ensure_utc(gap.last_at)
        went = last + timedelta(days=QUIET_DAYS)
        if went > inputs.now:
            continue
        if gap.returned_at is None:
            what = f"No activity since {_day(last)} — {_span(inputs.now - last)} so far."
        else:
            what = f"No activity from {_day(last)} for {_span(ensure_utc(gap.returned_at) - last)}."
        why = "A long silence counts against health and raises churn risk." if _hurts(inputs, "silence") else "A long silence."
        yield Milestone(
            "activity", "quiet", "Went quiet", went, what, why, tone="negative",
            evidence={"events": [gap.last_event_id] if gap.last_event_id else []},
            detail={"last_event_at": last, "ongoing": gap.returned_at is None, "key": last.isoformat()},
        )
        if gap.returned_at is not None:
            back = ensure_utc(gap.returned_at)
            yield Milestone(
                "activity", "returned", f"Came back after {_span(back - last)}", back,
                f"Their first event in {_span(back - last)}" + (f": {_words(gap.returned_type)}." if gap.returned_type else "."),
                "Activity again after a long silence.", tone="positive", event_id=gap.returned_event_id,
                evidence={"events": [gap.returned_event_id] if gap.returned_event_id else []},
                detail={"quiet_days": round((back - last).total_seconds() / 86400), "key": back.isoformat()},
            )


# ------------------------------------------------------------------ the chain


def _link(milestones: list[Milestone], inputs: JourneyInputs) -> None:
    """Each milestone's place in time, the memories its event changed, what happened to
    health, and the lifecycle moves that followed — read along event → snapshot → stay."""
    points = sorted(inputs.points, key=lambda point: ensure_utc(point.taken_at))
    index = {point.id: position for position, point in enumerate(points)}
    by_event: dict[str, Point] = {}
    for point in points:
        if point.event_id and point.event_id not in by_event:
            by_event[point.event_id] = point

    taken = [ensure_utc(point.taken_at) for point in points]
    stays_at: dict[str, list[Stay]] = {}
    point_of_stay: dict[str, Point] = {}
    for stay in inputs.stays:
        linked = str(getattr(stay.row, "snapshot_id", None) or "")
        point = points[index[linked]] if linked in index else None
        if point is None:
            entered = ensure_utc(stay.row.entered_at)
            position = bisect_left(taken, entered)
            if position < len(points) and (taken[position] - entered).total_seconds() <= LINK_SECONDS:
                point = points[position]
        if point is not None:
            stays_at.setdefault(point.id, []).append(stay)
            point_of_stay[stay.row.id] = point

    placing = placements(inputs.stays)
    created: dict[str, list[Any]] = {}
    for memory in inputs.memories:
        event = _first_event(memory)
        if event:
            created.setdefault(event, []).append(memory)
    repeated: dict[str, list[Any]] = {}
    for version, memory in inputs.versions:
        event = getattr(version, "source_event_id", None)
        if event:
            repeated.setdefault(str(event), []).append(memory)

    for milestone in milestones:
        point: Point | None = None
        if milestone.category == "health" and milestone.point_id in index:
            point = points[index[milestone.point_id]]
        elif milestone.category == "lifecycle":
            point = point_of_stay.get(str(milestone.detail.get("stay_id") or ""))
            if point is not None:
                milestone.point_id = point.id
                milestone.at = point.at or milestone.at
        elif milestone.event_id:
            point = by_event.get(milestone.event_id)
        if point is not None and milestone.event_id is None:
            milestone.event_id = point.event_id

        # Which memories changed: the milestone's own, then whatever else its event wrote.
        if milestone.event_id:
            refs = {ref["id"]: ref for ref in milestone.memories}
            for memory in created.get(milestone.event_id, []):
                refs.setdefault(memory.id, _memory_ref(memory, "created"))
            for memory in repeated.get(milestone.event_id, []):
                refs.setdefault(memory.id, _memory_ref(memory, "reported again"))
            milestone.memories = list(refs.values())[:8]
            milestone.evidence.setdefault("events", [])
            if milestone.event_id not in milestone.evidence["events"]:
                milestone.evidence["events"].append(milestone.event_id)

        if point is None:
            processed = milestone.recorded_at or milestone.at
            if (
                milestone.event_id
                and milestone.category not in ("account", "activity")
                and taken
                and taken[0] <= ensure_utc(processed)
            ):
                # Snapshots were being taken when its event was processed, and none was:
                # nothing material moved. (Before the first snapshot, nothing was measured.)
                milestone.detail["health_unchanged"] = True
            continue
        position = index[point.id]
        before = points[position - 1] if position > 0 else None
        milestone.health = _health_at(before, point)
        snapshots = milestone.evidence.setdefault("snapshots", [])
        if point.id not in snapshots:
            snapshots.append(point.id)
        if milestone.category != "lifecycle":
            moved = [
                stay for stay in stays_at.get(point.id, [])
                if stay.row.previous_state and str(stay.row.source) != "initial" and stay.row.id not in placing
            ]
            milestone.transitions = [_transition(stay, inputs) for stay in moved]
            if moved:
                milestone.evidence.setdefault("states", []).extend(stay.row.id for stay in moved)


def _health_at(before: Point | None, point: Point) -> dict[str, Any]:
    def side(values: dict[str, Any] | None) -> dict[str, Any] | None:
        if values is None:
            return None
        score = _number(values.get("health.score"))
        return {"score": round(score, 1) if score is not None else None, "band": values.get("health.band")}

    after = side(point.values)
    earlier = side(before.values) if before is not None else None
    delta = None
    if after and earlier and after["score"] is not None and earlier["score"] is not None:
        delta = round(after["score"] - earlier["score"], 1)
    drivers: list[str] = []
    # What moved with it — the causes: problems, intents, plan, feedback, goals, activity. Not
    # the forecast's outputs (signals), and nothing for a first snapshot, which has no before.
    for entry in (point.changes or []) if before is not None else []:
        fact = str(entry.get("fact", ""))
        if fact.startswith(("health.", "state.", "signals.", LIFECYCLE_PREFIX)) or fact.endswith(DAYS_SUFFIX):
            continue
        text = sentence(fact, entry.get("after"))
        if text and text not in drivers:
            drivers.append(text)
        if len(drivers) >= DRIVERS:
            break
    return {"before": earlier, "after": after, "delta": delta, "drivers": drivers, "snapshot_id": point.id}


# ------------------------------------------------------------------ readers


def cited_ids(milestones: Iterable[Milestone]) -> set[str]:
    """Every memory and goal the milestones name — what a reader's hidden set is computed over."""
    found: set[str] = set()
    for milestone in milestones:
        found.update(ident for ident in (milestone.subject, milestone.before_id) if ident)
        found.update(ref["id"] for ref in milestone.memories)
        found.update(milestone.evidence.get("memories", []))
        found.update(milestone.evidence.get("goals", []))
    return found


def shape(milestones: Sequence[Milestone], hidden: Iterable[str]) -> tuple[list[Milestone], int]:
    """The milestones one reader may see, and how many were withheld from them.

    A milestone about a memory or goal the reader may not see is dropped and counted; one
    that also quotes a hidden memory ("was: …") is shown without the quote; hidden memories
    leave its lists.
    """
    hidden = frozenset(hidden)
    if not hidden:
        return list(milestones), 0
    shown: list[Milestone] = []
    withheld = 0
    for milestone in milestones:
        if milestone.subject is not None and milestone.subject in hidden:
            withheld += 1
            continue
        changes: dict[str, Any] = {}
        if milestone.before_id is not None and milestone.before_id in hidden:
            changes["what"] = milestone.what_masked or milestone.what
            changes["before_id"] = None
        changes["memories"] = [ref for ref in milestone.memories if ref["id"] not in hidden]
        changes["evidence"] = {
            name: [ident for ident in ids if ident not in hidden] for name, ids in milestone.evidence.items()
        }
        shown.append(replace(milestone, **changes))
    return shown, withheld


# ------------------------------------------------------------------ present


def within(milestones: Iterable[Milestone], since: datetime | None, until: datetime | None) -> list[Milestone]:
    return [
        milestone
        for milestone in milestones
        if (since is None or ensure_utc(milestone.at) >= ensure_utc(since))
        and (until is None or ensure_utc(milestone.at) <= ensure_utc(until))
    ]


def select(
    milestones: Sequence[Milestone], *, limit: int, min_importance: float = 0.0
) -> tuple[list[Milestone], int]:
    """The milestones worth showing, oldest first: above ``min_importance``, and when there
    are more than ``limit``, the most important of them. Returns them and how many qualified."""
    qualified = [milestone for milestone in milestones if milestone.importance >= min_importance]
    chosen = qualified
    if len(qualified) > limit:
        chosen = sorted(qualified, key=lambda item: (item.importance, ensure_utc(item.at)), reverse=True)[:limit]
    chosen = sorted(chosen, key=_order)
    return chosen, len(qualified)


def counts(milestones: Iterable[Milestone]) -> dict[str, int]:
    found: dict[str, int] = {}
    for milestone in milestones:
        found[milestone.category] = found.get(milestone.category, 0) + 1
    return found


def summarise(milestones: Sequence[Milestone], *, lead: str, highlights: int = 6) -> str:
    """"Customer since 20 Jul 2026 · 9 milestones: started using Shopify (1 Sep); first
    Shopify problem (5 Sep); …" — the most important few, in the order they happened."""
    if not milestones:
        return f"{lead}: nothing that stands out yet."
    top = sorted(milestones, key=lambda item: (item.importance, ensure_utc(item.at)), reverse=True)[:highlights]
    top.sort(key=lambda item: ensure_utc(item.at))
    parts = [f"{_lower_first(item.title)} ({_day(ensure_utc(item.at))})" for item in top]
    count = len(milestones)
    return f"{lead} · {count} milestone{'s' if count != 1 else ''}: {'; '.join(parts)}."


def markdown(journey: dict[str, Any]) -> str:
    """The journey as a page — for a prompt, a ticket or a handover."""
    name = (journey.get("customer") or {}).get("name") or journey.get("customer_id") or "Customer"
    lines = [f"# {name} — journey", "", str(journey.get("summary") or "")]
    month = None
    for item in journey.get("milestones") or []:
        at = item["at"] if isinstance(item["at"], datetime) else _parse(item["at"])
        if at is None:
            continue
        heading = f"{at:%B %Y}"
        if heading != month:
            lines += ["", f"## {heading}"]
            month = heading
        line = f"- **{at.day} {at:%b}** — {item['title']}. {item.get('what_happened') or ''}".rstrip()
        extras: list[str] = []
        if item.get("why_it_matters"):
            extras.append(str(item["why_it_matters"]))
        health = item.get("health") or {}
        before, after = health.get("before") or {}, health.get("after") or {}
        if (
            item.get("category") != "health"
            and before.get("score") is not None
            and after.get("score") is not None
            and round(float(after["score"])) != round(float(before["score"]))
        ):
            extras.append(f"Health {float(before['score']):.0f} → {float(after['score']):.0f}.")
        for move in item.get("transitions") or []:
            if item.get("category") != "lifecycle":
                extras.append(f"{move['label']}: {_words(move['before'])} → {_words(move['after'])}.")
        if extras:
            line += " " + " ".join(extras)
        lines.append(line)
    if journey.get("withheld"):
        lines += ["", f"_{journey['withheld']} milestone(s) concern memories you may not read._"]
    return "\n".join(lines).strip() + "\n"


__all__ = [
    "CATEGORIES",
    "FirstUse",
    "Gap",
    "JourneyInputs",
    "Milestone",
    "Point",
    "Stay",
    "cited_ids",
    "counts",
    "detect",
    "markdown",
    "select",
    "shape",
    "summarise",
    "within",
]
