"""Typed results.

Plain dataclasses rather than a validation framework: the SDK should add one dependency,
not a runtime schema engine, and every field maps 1:1 to the documented API response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _get(data: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


@dataclass(slots=True)
class TrackedEvent:
    event_id: str
    status: str
    customer_id: str
    importance: float = 0.0
    queued: bool = False

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TrackedEvent:
        return cls(
            event_id=data["event_id"],
            status=data.get("status", "accepted"),
            customer_id=data.get("customer_id", ""),
            importance=float(data.get("importance", 0.0)),
            queued=bool(data.get("queued", False)),
        )


@dataclass(slots=True)
class ConditionResult:
    """A condition evaluated against one customer, with the clauses that decided it.

    ``outcome`` is ``"true"``, ``"false"`` or ``"unknown"`` — unknown when a fact the
    condition reads has no value yet. Act on :attr:`matched`, which treats unknown as false;
    the trace in :attr:`explanation` says which clause could not be decided.
    """

    condition: str
    outcome: str
    matched: bool
    explanation: str
    evidence: list[str] = field(default_factory=list)
    leaves: list[dict[str, Any]] = field(default_factory=list)
    withheld_facts: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.matched

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ConditionResult:
        evaluation = data.get("evaluation") or {}
        return cls(
            condition=data.get("condition", ""),
            outcome=evaluation.get("outcome", "unknown"),
            matched=bool(evaluation.get("matched", False)),
            explanation=evaluation.get("explanation", ""),
            evidence=list(evaluation.get("evidence") or []),
            leaves=list(evaluation.get("leaves") or []),
            withheld_facts=list(data.get("withheld_facts") or []),
        )


@dataclass(slots=True)
class LifecycleState:
    """Where a customer is on a lifecycle track, and why.

    ``reasons`` are the decisive clauses in words — "3 unresolved problems", "activity down
    47%" — ready to show a person; ``reason`` is the precise trace.
    """

    state: str
    previous_state: str | None = None
    entered_at: str | None = None
    source: str = "auto"
    transition: str | None = None
    reason: str | None = None
    evidence: list[str] = field(default_factory=list)
    pinned: bool = False
    pinned_until: str | None = None
    track: str = "lifecycle"
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> LifecycleState:
        return cls(
            track=data.get("track", "lifecycle"),
            reasons=list(data.get("reasons") or []),
            state=data.get("state", ""),
            previous_state=data.get("previous_state"),
            entered_at=data.get("entered_at"),
            source=data.get("source", "auto"),
            transition=data.get("transition"),
            reason=data.get("reason"),
            evidence=list(data.get("evidence") or []),
            pinned=bool(data.get("pinned", False)),
            pinned_until=data.get("pinned_until"),
        )


@dataclass(slots=True)
class Change:
    """One thing that changed about a customer: before, after, when, and the evidence."""

    type: str
    kind: str
    title: str
    detected_at: str
    before: str | None = None
    after: str | None = None
    evidence: list[str] = field(default_factory=list)
    source: str = "memory"
    track: str | None = None
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Change:
        return cls(
            type=data.get("type", ""),
            kind=data.get("kind", ""),
            title=data.get("title", ""),
            detected_at=data.get("detected_at", ""),
            before=data.get("before"),
            after=data.get("after"),
            evidence=list(data.get("evidence") or []),
            source=data.get("source", "memory"),
            track=data.get("track"),
            reasons=list(data.get("reasons") or []),
            detail=dict(data.get("detail") or {}),
            importance=float(data.get("importance") or 0.0),
        )


@dataclass(slots=True)
class CustomerChanges:
    """What changed about a customer in a window, and what they looked like then and now.

    ``summary`` is one sentence to read before a call; ``changes`` are newest first (or
    most important first, with ``order="importance"``); ``then`` and ``now`` are the
    customer at each end — plan, health, lifecycle and tracks, problems, goals, channel.
    """

    customer_id: str
    summary: str
    changes: list[Change]
    since: str
    until: str
    basis: str
    label: str
    then: dict[str, Any]
    now: dict[str, Any]
    counts: dict[str, int] = field(default_factory=dict)
    withheld: int = 0
    truncated: bool = False
    note: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CustomerChanges:
        window = data.get("window") or {}
        return cls(
            customer_id=data.get("customer_id", ""),
            summary=data.get("summary", ""),
            changes=[Change.from_api(item) for item in data.get("changes") or []],
            since=window.get("since", ""),
            until=window.get("until", ""),
            basis=window.get("basis", ""),
            label=window.get("label", ""),
            note=window.get("note"),
            then=dict(data.get("then") or {}),
            now=dict(data.get("now") or {}),
            counts=dict(data.get("counts") or {}),
            withheld=int(data.get("withheld") or 0),
            truncated=bool(data.get("truncated", False)),
            raw=data,
        )

    def of_type(self, *types: str) -> list[Change]:
        return [change for change in self.changes if change.type in types]


@dataclass(slots=True)
class Customer360:
    """Everything worth knowing about one customer, from a single call.

    Sections are reachable as attributes for the common ones and through
    :meth:`section` for the rest, so a caller that asked for three sections is not
    tempted to read eight nulls.
    """

    customer: dict[str, Any]
    summary: str = ""
    sections: dict[str, Any] = field(default_factory=dict)
    withheld: int = 0
    generated_at: str | None = None

    def section(self, name: str, default: Any = None) -> Any:
        """A section, or ``default`` if it was not built.

        ``None`` for a section that was not requested is different from ``[]`` for one that
        was and found nothing, so the default is explicit rather than assumed.
        """
        return self.sections.get(name, default)

    @property
    def health_score(self) -> float | None:
        health = self.sections.get("health")
        return float(health["score"]) if health else None

    @property
    def is_at_risk(self) -> bool:
        health = self.sections.get("health") or {}
        return health.get("band") in ("at_risk", "critical")

    @property
    def active_problems(self) -> list[dict[str, Any]]:
        return list(self.sections.get("active_problems") or [])

    @property
    def recommended_actions(self) -> list[dict[str, Any]]:
        return list(self.sections.get("recommended_actions") or [])

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Customer360:
        return cls(
            customer=dict(data.get("customer") or {}),
            summary=data.get("summary", ""),
            sections=dict(data.get("sections") or {}),
            withheld=int(data.get("withheld", 0)),
            generated_at=data.get("generated_at"),
        )


@dataclass(slots=True)
class MemoryPlan:
    """One statement the engine found in an event, and what it would do with it."""

    content: str
    type: str
    action: str
    reason: str
    importance: float = 0.0
    confidence: float = 0.0
    similarity: float = 0.0
    rule: str | None = None
    memory_id: str | None = None
    closest_memory_id: str | None = None
    closest_content: str | None = None
    sensitivity: str = "normal"
    restricted_by: str | None = None
    extracted_by: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> MemoryPlan:
        return cls(
            content=data.get("content", ""),
            type=data.get("type", ""),
            action=data.get("action", ""),
            reason=data.get("reason", ""),
            importance=float(data.get("importance", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            similarity=float(data.get("similarity", 0.0)),
            rule=data.get("rule"),
            memory_id=data.get("memory_id"),
            closest_memory_id=data.get("closest_memory_id"),
            closest_content=data.get("closest_content"),
            sensitivity=data.get("sensitivity", "normal"),
            restricted_by=data.get("restricted_by"),
            extracted_by=data.get("extracted_by"),
        )


@dataclass(slots=True)
class EventExplanation:
    """Why an event did, or would, become a memory.

    Returned by :meth:`MemoryClient.preview`, which writes nothing. ``stop_reason`` is the field
    to read first: when it is set, nothing else happened and it says why.
    """

    would_process: bool = False
    stop_reason: str | None = None
    summary: str = ""
    importance: float = 0.0
    threshold: float = 0.0
    text: str | None = None
    text_length: int = 0
    redacted: bool = False
    redactions: list[dict[str, Any]] = field(default_factory=list)
    memories: list[MemoryPlan] = field(default_factory=list)
    memory_count: int = 0
    entities: list[dict[str, Any]] = field(default_factory=list)
    entity_count: int = 0
    duration_ms: float = 0.0

    def __bool__(self) -> bool:
        """Truthy when the event would produce something, so ``if not preview:`` reads right."""
        return self.would_process and bool(self.memories)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> EventExplanation:
        return cls(
            would_process=bool(data.get("would_process", False)),
            stop_reason=data.get("stop_reason"),
            summary=data.get("summary", ""),
            importance=float(data.get("importance", 0.0)),
            threshold=float(data.get("threshold", 0.0)),
            text=data.get("text"),
            text_length=int(data.get("text_length", 0)),
            redacted=bool(data.get("redacted", False)),
            redactions=list(data.get("redactions") or []),
            memories=[MemoryPlan.from_api(item) for item in data.get("memories") or []],
            memory_count=int(data.get("memory_count", 0)),
            entities=list(data.get("entities") or []),
            entity_count=int(data.get("entity_count", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
        )


@dataclass(slots=True)
class Memory:
    id: str
    type: str
    content: str
    importance: float
    confidence: float
    status: str = "active"
    evidence_count: int = 1
    source_event_ids: list[str] = field(default_factory=list)
    last_seen_at: str | None = None
    score: float | None = None
    retrieved_by: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Memory:
        return cls(
            id=data["id"],
            type=data.get("type", "fact"),
            content=data.get("content", ""),
            importance=float(data.get("importance", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            status=data.get("status", "active"),
            evidence_count=int(data.get("evidence_count", 1)),
            source_event_ids=list(data.get("source_event_ids") or []),
            last_seen_at=data.get("last_seen_at"),
            score=data.get("score"),
            retrieved_by=list(data.get("retrieved_by") or []),
        )


@dataclass(slots=True)
class QueryResult:
    answer: str
    confidence: float
    memories: list[Memory] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] | None = None
    # The recorded agent run — ``client.explain_run(run_id)`` says why it answered so.
    run_id: str | None = None

    @property
    def has_answer(self) -> bool:
        return bool(self.answer) and "not enough memory" not in self.answer.lower()

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> QueryResult:
        return cls(
            answer=data.get("answer", ""),
            confidence=float(data.get("confidence", 0.0)),
            memories=[Memory.from_api(item) for item in data.get("memories", [])],
            source_event_ids=[item["event_id"] for item in data.get("sources", [])],
            trace=data.get("trace"),
            run_id=data.get("run_id"),
        )


@dataclass(slots=True)
class CustomerContext:
    important_facts: list[str] = field(default_factory=list)
    active_problems: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    prompt_text: str | None = None
    token_count: int = 0
    truncated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> CustomerContext:
        context = data.get("customer_context") or {}
        return cls(
            important_facts=list(context.get("important_facts") or []),
            active_problems=list(context.get("active_problems") or []),
            preferences=list(context.get("preferences") or []),
            goals=list(context.get("goals") or []),
            recent_events=list(context.get("recent_events") or []),
            relationships=list(context.get("relationships") or []),
            memory_ids=list(context.get("memory_ids") or []),
            prompt_text=data.get("prompt_text"),
            token_count=int(data.get("token_count", 0)),
            truncated=bool(data.get("truncated", False)),
            raw=data,
            run_id=data.get("run_id") or context.get("run_id"),
        )


@dataclass(slots=True)
class Health:
    customer_id: str
    score: float
    band: str
    churn_risk: float
    explanation: str = ""
    factors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_at_risk(self) -> bool:
        return self.band in ("at_risk", "critical")

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Health:
        return cls(
            customer_id=_get(data, "customer_id", default=""),
            score=float(data.get("score", 0.0)),
            band=data.get("band", "watch"),
            churn_risk=float(data.get("churn_risk", 0.0)),
            explanation=data.get("explanation", ""),
            factors=list(data.get("factors") or []),
        )


@dataclass(slots=True)
class Customer:
    id: str
    external_id: str
    email: str | None = None
    name: str | None = None
    last_event_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Customer:
        return cls(
            id=data["id"],
            external_id=data.get("external_id", ""),
            email=data.get("email"),
            name=data.get("name"),
            last_event_at=data.get("last_event_at"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class Signal:
    """One observation pointing at a likely outcome."""

    key: str
    label: str
    direction: str
    strength: float
    horizon_days: int
    rationale: str
    memory_ids: list[str] = field(default_factory=list)
    observed: float = 0.0

    @property
    def is_risk(self) -> bool:
        return self.direction == "risk"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Signal:
        return cls(
            key=data["key"],
            label=data.get("label", ""),
            direction=data.get("direction", "risk"),
            strength=float(data.get("strength", 0.0)),
            horizon_days=int(data.get("horizon_days", 30)),
            rationale=data.get("rationale", ""),
            memory_ids=list(data.get("memory_ids") or []),
            observed=float(data.get("observed", 0.0)),
        )


@dataclass(slots=True)
class SignalReport:
    """Where a customer is heading, and why."""

    customer_id: str
    trajectory: str
    churn_risk: float
    expansion_score: float
    confidence: float
    headline: str = ""
    health_score: float = 0.0
    signals: list[Signal] = field(default_factory=list)
    measurements: dict[str, float] = field(default_factory=dict)
    series: list[dict[str, Any]] = field(default_factory=list)
    computed_at: str | None = None

    @property
    def is_declining(self) -> bool:
        return self.trajectory == "declining"

    @property
    def risks(self) -> list[Signal]:
        return [signal for signal in self.signals if signal.is_risk]

    @property
    def opportunities(self) -> list[Signal]:
        return [signal for signal in self.signals if not signal.is_risk]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SignalReport:
        return cls(
            customer_id=_get(data, "customer_id", default=""),
            trajectory=data.get("trajectory", "steady"),
            churn_risk=float(data.get("churn_risk", 0.0)),
            expansion_score=float(data.get("expansion_score", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            headline=data.get("headline", ""),
            health_score=float(data.get("health_score", 0.0)),
            signals=[Signal.from_api(item) for item in data.get("signals") or []],
            measurements=dict(data.get("measurements") or {}),
            series=list(data.get("series") or []),
            computed_at=data.get("computed_at"),
        )


@dataclass(slots=True)
class Recommendation:
    """Something to do about a customer, and the evidence for doing it."""

    key: str
    action: str
    rationale: str
    category: str
    urgency: float
    priority: str
    memory_ids: list[str] = field(default_factory=list)
    goal_ids: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    playbook: list[str] = field(default_factory=list)

    @property
    def is_urgent(self) -> bool:
        return self.priority == "now"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Recommendation:
        return cls(
            key=data["key"],
            action=data.get("action", ""),
            rationale=data.get("rationale", ""),
            category=data.get("category", ""),
            urgency=float(data.get("urgency", 0.0)),
            priority=data.get("priority", "when_you_can"),
            memory_ids=list(data.get("memory_ids") or []),
            goal_ids=list(data.get("goal_ids") or []),
            signals=list(data.get("signals") or []),
            playbook=list(data.get("playbook") or []),
        )


@dataclass(slots=True)
class Goal:
    """Something a customer said they were trying to do."""

    id: str
    customer_id: str
    statement: str
    status: str
    progress: float = 0.0
    confidence: float = 0.0
    keywords: list[str] = field(default_factory=list)
    memory_id: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    opened_at: str | None = None
    last_signal_at: str | None = None
    closed_at: str | None = None
    closed_reason: str | None = None
    overridden: bool = False

    @property
    def is_live(self) -> bool:
        return self.status not in ("achieved", "abandoned")

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Goal:
        return cls(
            id=data["id"],
            customer_id=data.get("customer_id", ""),
            statement=data.get("statement", ""),
            status=data.get("status", "open"),
            progress=float(data.get("progress", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            keywords=list(data.get("keywords") or []),
            memory_id=data.get("memory_id"),
            evidence=list(data.get("evidence") or []),
            opened_at=data.get("opened_at"),
            last_signal_at=data.get("last_signal_at"),
            closed_at=data.get("closed_at"),
            closed_reason=data.get("closed_reason"),
            overridden=bool(data.get("overridden", False)),
        )


@dataclass(slots=True)
class PriorSession:
    id: str
    agent: str
    summary: str
    turn_count: int = 0
    started_at: str | None = None
    closed_at: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PriorSession:
        return cls(
            id=data["id"],
            agent=data.get("agent", ""),
            summary=data.get("summary", ""),
            turn_count=int(data.get("turn_count", 0)),
            started_at=data.get("started_at"),
            closed_at=data.get("closed_at"),
        )


@dataclass(slots=True)
class SessionContext:
    """What the agent should know before it writes its first word."""

    text: str = ""
    memory_ids: list[str] = field(default_factory=list)
    token_estimate: int = 0
    truncated: bool = False
    prior_sessions: list[PriorSession] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> SessionContext | None:
        if not data:
            return None
        return cls(
            text=data.get("text", ""),
            memory_ids=list(data.get("memory_ids") or []),
            token_estimate=int(data.get("token_estimate", 0)),
            truncated=bool(data.get("truncated", False)),
            prior_sessions=[
                PriorSession.from_api(item) for item in data.get("prior_sessions") or []
            ],
        )


@dataclass(slots=True)
class Turn:
    id: str
    role: str
    content: str
    occurred_at: str | None = None
    event_id: str | None = None
    retrieved_memory_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Turn:
        return cls(
            id=data["id"],
            role=data.get("role", "user"),
            content=data.get("content", ""),
            occurred_at=data.get("occurred_at"),
            event_id=data.get("event_id"),
            retrieved_memory_ids=list(data.get("retrieved_memory_ids") or []),
        )


@dataclass(slots=True)
class AgentSession:
    """A conversation, and the memory it carries in and out."""

    id: str
    customer_id: str
    agent: str = "agent"
    status: str = "open"
    external_id: str | None = None
    turn_count: int = 0
    started_at: str | None = None
    last_active_at: str | None = None
    closed_at: str | None = None
    summary: str | None = None
    summary_memory_id: str | None = None
    memory_ids: list[str] = field(default_factory=list)
    resumed: bool = False
    context: SessionContext | None = None
    turns: list[Turn] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def prompt_text(self) -> str:
        """The briefing to put in front of your model, including earlier conversations."""
        if self.context is None:
            return ""
        blocks = [self.context.text]
        for prior in self.context.prior_sessions:
            blocks.append(f"Earlier conversation ({prior.agent}): {prior.summary}")
        return "\n\n".join(block for block in blocks if block)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AgentSession:
        return cls(
            id=data["id"],
            customer_id=data.get("customer_id", ""),
            agent=data.get("agent", "agent"),
            status=data.get("status", "open"),
            external_id=data.get("external_id"),
            turn_count=int(data.get("turn_count", 0)),
            started_at=data.get("started_at"),
            last_active_at=data.get("last_active_at"),
            closed_at=data.get("closed_at"),
            summary=data.get("summary"),
            summary_memory_id=data.get("summary_memory_id"),
            memory_ids=list(data.get("memory_ids") or []),
            resumed=bool(data.get("resumed", False)),
            context=SessionContext.from_api(data.get("context")),
            turns=[Turn.from_api(item) for item in data.get("turns") or []],
        )


@dataclass(slots=True)
class TurnResult:
    session_id: str
    turn: Turn
    context: SessionContext | None = None
    answer: str | None = None
    answer_confidence: float | None = None
    event_id: str | None = None
    turn_count: int = 0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TurnResult:
        return cls(
            session_id=data.get("session_id", ""),
            turn=Turn.from_api(data["turn"]),
            context=SessionContext.from_api(data.get("context")),
            answer=data.get("answer"),
            answer_confidence=data.get("answer_confidence"),
            event_id=data.get("event_id"),
            turn_count=int(data.get("turn_count", 0)),
        )


# ------------------------------------------------------------------- agents (§26 3)


@dataclass(slots=True)
class Approval:
    """A person's decision on an action an agent asked permission for."""

    id: str
    customer_id: str
    action: str
    status: str  # pending | approved | rejected | expired | used
    request: dict[str, Any] = field(default_factory=dict)
    reasons: list[dict[str, Any]] = field(default_factory=list)
    agent: str | None = None
    note: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    used_at: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    # What a reviewer reads before deciding (§26 4.5): the cited memories in their own words,
    # how many they may not read, the customer as last recorded, and the waiting action.
    evidence_memories: list[dict[str, Any]] = field(default_factory=list)
    withheld_evidence: int = 0
    customer: dict[str, Any] | None = None
    action_id: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Approval:
        return cls(
            id=data["id"],
            customer_id=data.get("customer_id", ""),
            action=data.get("action", ""),
            status=data.get("status", "pending"),
            request=dict(data.get("request") or {}),
            reasons=list(data.get("reasons") or []),
            agent=data.get("agent"),
            note=data.get("note"),
            decided_by=data.get("decided_by"),
            decided_at=data.get("decided_at"),
            used_at=data.get("used_at"),
            expires_at=data.get("expires_at"),
            created_at=data.get("created_at"),
            evidence_memories=list(data.get("evidence_memories") or []),
            withheld_evidence=int(data.get("withheld_evidence") or 0),
            customer=data.get("customer"),
            action_id=data.get("action_id"),
        )


@dataclass(slots=True)
class AgentAction:
    """An action through the gateway (§26 4.5): requested, then ``allowed``,
    ``pending_approval`` or ``denied``; an allowed one ends ``done``, ``failed`` or
    ``cancelled`` when reported back. :attr:`next_step` says what to do now."""

    id: str
    customer_id: str
    action: str
    status: str
    decision: str
    summary: str
    next_step: str
    request: dict[str, Any] = field(default_factory=dict)
    reasons: list[dict[str, Any]] = field(default_factory=list)
    approval: Approval | None = None
    check_id: str | None = None
    agent: str | None = None
    idempotency_key: str | None = None
    outcome_note: str | None = None
    external_ref: str | None = None
    created_at: str | None = None
    completed_at: str | None = None

    def __bool__(self) -> bool:
        return self.allowed

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"

    @property
    def denied(self) -> bool:
        return self.status == "denied"

    @property
    def waiting(self) -> bool:
        return self.status == "pending_approval"

    @property
    def evidence(self) -> list[str]:
        return list(dict.fromkeys(ident for reason in self.reasons for ident in reason.get("evidence") or []))

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AgentAction:
        approval = data.get("approval")
        return cls(
            id=data["id"],
            customer_id=data.get("customer_id", ""),
            action=data.get("action", ""),
            status=data.get("status", ""),
            decision=data.get("decision", ""),
            summary=data.get("summary", ""),
            next_step=data.get("next_step", ""),
            request=dict(data.get("request") or {}),
            reasons=list(data.get("reasons") or []),
            approval=Approval.from_api(approval) if approval else None,
            check_id=data.get("check_id"),
            agent=data.get("agent"),
            idempotency_key=data.get("idempotency_key"),
            outcome_note=data.get("outcome_note"),
            external_ref=data.get("external_ref"),
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass(slots=True)
class ActionCheck:
    """Whether an agent may take an action, and why. Truthy only when allowed.

    ``decision`` is ``allow``, ``require_approval`` or ``deny``. Every rule that objected
    is in :attr:`reasons`, with the memory ids behind it in :attr:`evidence`; :attr:`summary`
    is the sentence to tell a person — or the model — why.
    """

    id: str
    customer_id: str
    action: str
    decision: str
    allowed: bool
    summary: str
    reasons: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    approval: Approval | None = None
    agent: str | None = None
    profile: str | None = None
    request: dict[str, Any] = field(default_factory=dict)
    checked_at: str | None = None

    def __bool__(self) -> bool:
        return self.allowed

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    @property
    def requires_approval(self) -> bool:
        return self.decision == "require_approval"

    @property
    def rules(self) -> list[str]:
        return [str(reason.get("rule")) for reason in self.reasons]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ActionCheck:
        approval = data.get("approval")
        return cls(
            id=data.get("id", ""),
            customer_id=data.get("customer_id", ""),
            action=data.get("action", ""),
            decision=data.get("decision", "deny"),
            allowed=bool(data.get("allowed", False)),
            summary=data.get("summary", ""),
            reasons=list(data.get("reasons") or []),
            evidence=list(data.get("evidence") or []),
            approval=Approval.from_api(approval) if approval else None,
            agent=data.get("agent"),
            profile=data.get("profile"),
            request=dict(data.get("request") or {}),
            checked_at=data.get("checked_at"),
        )


@dataclass(slots=True)
class AgentRun:
    """One answer or briefing an agent asked for, as it was recorded."""

    id: str
    kind: str  # query | context
    query: str
    customer_id: str | None = None
    answer: str | None = None
    agent: str | None = None
    session_id: str | None = None
    snapshot_id: str | None = None
    memory_count: int = 0
    cited_count: int = 0
    withheld: int = 0
    created_at: str | None = None
    memory_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AgentRun:
        return cls(
            id=data["id"],
            kind=data.get("kind", "query"),
            query=data.get("query", ""),
            customer_id=data.get("customer_id"),
            answer=data.get("answer"),
            agent=data.get("agent"),
            session_id=data.get("session_id"),
            snapshot_id=data.get("snapshot_id"),
            memory_count=int(data.get("memory_count", 0)),
            cited_count=int(data.get("cited_count", 0)),
            withheld=int(data.get("withheld", 0)),
            created_at=data.get("created_at"),
            memory_ids=list(data.get("memory_ids") or []),
            trace=dict(data.get("trace") or {}),
        )


@dataclass(slots=True)
class RunExplanation:
    """Why an agent said what it said: the memories as they were then and are now, what
    was held back, the customer's recorded state at that moment, and the checks around it."""

    run: AgentRun
    narrative: list[str] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    held_back: dict[str, Any] = field(default_factory=dict)
    state_then: dict[str, Any] | None = None
    checks: list[ActionCheck] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.narrative)

    @property
    def changed_since(self) -> list[dict[str, Any]]:
        """The memories the run relied on that have changed since — the usual answer to
        "why did it say that?" when it is no longer true."""
        return [memory for memory in self.memories if memory.get("changed_since")]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RunExplanation:
        return cls(
            run=AgentRun.from_api(data["run"]),
            narrative=list(data.get("narrative") or []),
            memories=list(data.get("memories") or []),
            held_back=dict(data.get("held_back") or {}),
            state_then=data.get("state_then"),
            checks=[ActionCheck.from_api(item) for item in data.get("checks") or []],
        )


@dataclass(slots=True)
class RunTrace:
    """Why did my agent do this? What it was given — ``cited``, ``given`` (in a context) or
    ``not_cited`` — what it was **not** given and why (``below_cut``, ``type_cap``,
    ``token_budget``, ``section_cap``, ``duplicate``, ``superseded``, ``expired``,
    ``withheld_restricted``, ``withheld_profile``), and the decision it came to.

        trace = client.run_trace(result.run_id)
        for item in trace.ignored_because("superseded"):
            print(item["why"])  # "Superseded on 12 Sep 2026 by a newer memory: … — which the agent was given (#1)."
    """

    run: AgentRun
    question: str
    given: list[dict[str, Any]] = field(default_factory=list)
    ignored: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)
    narrative: list[str] = field(default_factory=list)
    cut: dict[str, Any] = field(default_factory=dict)
    held_back: dict[str, Any] = field(default_factory=dict)
    state_then: dict[str, Any] | None = None
    checks: list[ActionCheck] = field(default_factory=list)
    recorded: bool = True

    @property
    def text(self) -> str:
        return "\n".join(self.narrative)

    @property
    def cited(self) -> list[dict[str, Any]]:
        return [item for item in self.given if item.get("verdict") == "cited"]

    @property
    def confidence(self) -> float | None:
        return self.decision.get("confidence")

    def ignored_because(self, *reasons: str) -> list[dict[str, Any]]:
        return [item for item in self.ignored if item.get("reason") in reasons]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RunTrace:
        return cls(
            run=AgentRun.from_api(data["run"]),
            question=data.get("question", ""),
            given=list(data.get("given") or []),
            ignored=list(data.get("ignored") or []),
            decision=dict(data.get("decision") or {}),
            narrative=list(data.get("narrative") or []),
            cut=dict(data.get("cut") or {}),
            held_back=dict(data.get("held_back") or {}),
            state_then=data.get("state_then"),
            checks=[ActionCheck.from_api(item) for item in data.get("checks") or []],
            recorded=bool(data.get("recorded", True)),
        )


@dataclass(slots=True)
class AgentProfile:
    """What an agent is for: the memory it may read and the actions it may take."""

    id: str
    name: str
    description: str | None = None
    readable_types: list[str] = field(default_factory=list)
    can_read_restricted: bool = False
    allowed_actions: list[str] = field(default_factory=list)
    denied_actions: list[str] = field(default_factory=list)
    keys: int = 0

    def may(self, action: str) -> bool:
        """Whether the profile's own lists permit ``action`` — rules may still object."""
        if action in self.denied_actions:
            return False
        return not self.allowed_actions or action in self.allowed_actions

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AgentProfile:
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            description=data.get("description"),
            readable_types=list(data.get("readable_types") or []),
            can_read_restricted=bool(data.get("can_read_restricted", False)),
            allowed_actions=list(data.get("allowed_actions") or []),
            denied_actions=list(data.get("denied_actions") or []),
            keys=int(data.get("keys", 0)),
        )
