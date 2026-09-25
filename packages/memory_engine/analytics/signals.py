"""Predictive signals: which way a customer is moving, and what is moving them.

Health answers "how is this customer?". This module answers the harder question — "what
is about to happen?" — by comparing two windows of the same memory graph against each
other. A customer sitting at 72 with three problems opened this fortnight and none the
fortnight before is in a different situation from a customer sitting at 72 whose last
problem was resolved a month ago, and only the second reading tells you which is which.

Everything here is a measurement of stored rows: counts inside windows, ages, deltas
against a stored snapshot. There is no model and no training data, so a signal can always
be traced to the memories that produced it, and the same history always yields the same
forecast.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from common.enums import MemoryType, SignalDirection, Trajectory
from common.time import days_between, ensure_utc, utcnow
from memory_engine.policy import WITHHELD
from nlp.answer import MemoryView
from nlp.tokenize import content_words, lemmatize

# Two equal windows, compared against each other. Fourteen days is long enough to survive
# a quiet week and short enough that a change is still actionable.
WINDOW_DAYS = 14
SILENCE_DAYS = 30
AGEING_PROBLEM_DAYS = 14
STALE_GOAL_DAYS = 30

# A health move smaller than this is noise, not a trend.
TRAJECTORY_HEALTH_DELTA = 5.0
TRAJECTORY_PRESSURE_DELTA = 0.25
# A single signal this strong, with nothing pulling the other way, sets the direction.
DECISIVE_SIGNAL = 0.7

UPGRADE_LEMMAS = {
    lemmatize(word)
    for word in ("upgrade", "expand", "expansion", "add seats", "more seats", "enterprise",
                 "annual", "renew", "scale", "additional")
}
CHURN_LEMMAS = {
    lemmatize(word)
    for word in ("cancel", "cancelled", "cancellation", "churn", "downgrade", "refund",
                 "terminate", "competitor", "switching", "leave", "leaving")
}

# How much each signal contributes to the blended score. Deliberately modest: no single
# observation should be able to carry a forecast on its own.
RISK_WEIGHTS = {
    "escalating_problems": 1.0,
    "ageing_open_problem": 0.7,
    "repeat_problem": 0.8,
    "churn_language": 1.2,
    "engagement_decay": 0.9,
    "silence": 0.8,
    "negative_feedback_trend": 0.7,
    "goal_stalled": 0.5,
    "health_slide": 1.0,
}
OPPORTUNITY_WEIGHTS = {
    "expansion_intent": 1.2,
    "adoption_growth": 0.9,
    "advocacy": 0.8,
    "goal_achieved": 0.7,
    "health_climb": 0.8,
}

# Sum of weights that would represent "everything is firing at full strength". Scores are
# normalised against this rather than against the number of signals that happened to fire,
# so one strong signal cannot pin the score at 1.0.
_RISK_NORMALISER = 3.5
_OPPORTUNITY_NORMALISER = 2.5


@dataclass(slots=True, frozen=True)
class ActivityWindow:
    """Event counts either side of the window boundary, plus the last time we heard anything."""

    recent_events: int = 0
    prior_events: int = 0
    last_event_at: datetime | None = None
    distinct_features: int = 0


@dataclass(slots=True, frozen=True)
class GoalSnapshot:
    """Just enough of a tracked goal for the forecast to take it into account."""

    id: str
    statement: str
    status: str
    progress: float
    last_signal_at: datetime


@dataclass(slots=True, frozen=True)
class Baseline:
    """Where this customer stood when we last looked, used for the deltas."""

    health_score: float
    churn_risk: float
    captured_at: datetime


@dataclass(slots=True, frozen=True)
class Signal:
    key: str
    label: str
    direction: SignalDirection
    strength: float
    horizon_days: int
    rationale: str
    memory_ids: tuple[str, ...] = ()
    observed: float = 0.0
    # (id, words) for a goal or memory quoted in the rationale — see :func:`mask_quotes`.
    quotes: tuple[tuple[str, str], ...] = ()

    def cited_ids(self) -> set[str]:
        return {*self.memory_ids, *(ident for ident, _ in self.quotes)}

    def redacted(self, hidden: AbstractSet[str]) -> Signal:
        if not hidden or not (self.cited_ids() & hidden):
            return self
        return replace(
            self,
            rationale=mask_quotes(self.rationale, self.quotes, hidden),
            memory_ids=tuple(ident for ident in self.memory_ids if ident not in hidden),
            quotes=tuple(quote for quote in self.quotes if quote[0] not in hidden),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "direction": str(self.direction),
            "strength": round(self.strength, 3),
            "horizon_days": self.horizon_days,
            "rationale": self.rationale,
            "memory_ids": list(self.memory_ids[:5]),
            "observed": round(self.observed, 3),
        }


@dataclass(slots=True)
class SignalReport:
    trajectory: Trajectory
    churn_risk: float
    expansion_score: float
    confidence: float
    signals: list[Signal] = field(default_factory=list)
    measurements: dict[str, float] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=utcnow)

    @property
    def risks(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == SignalDirection.RISK]

    @property
    def opportunities(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == SignalDirection.OPPORTUNITY]

    @property
    def headline(self) -> str:
        return headline(self)

    def cited_ids(self) -> set[str]:
        return {ident for signal in self.signals for ident in signal.cited_ids()}

    def redacted(self, hidden: AbstractSet[str]) -> SignalReport:
        """The report as a reader who may not see ``hidden`` sees it. The numbers stay —
        they were computed over everything, like health — and quoted words go."""
        if not hidden or not (self.cited_ids() & hidden):
            return self
        return replace(self, signals=[signal.redacted(hidden) for signal in self.signals])

    def as_dict(self) -> dict[str, Any]:
        return {
            "trajectory": str(self.trajectory),
            "churn_risk": round(self.churn_risk, 3),
            "expansion_score": round(self.expansion_score, 3),
            "confidence": round(self.confidence, 3),
            "headline": self.headline,
            "signals": [signal.as_dict() for signal in self.signals],
            "measurements": {key: round(value, 3) for key, value in self.measurements.items()},
            "computed_at": self.computed_at.isoformat(),
        }


def mask_quotes(text: str, quotes: Iterable[tuple[str, str]], hidden: AbstractSet[str]) -> str:
    """``text`` with the words quoted from each hidden id replaced by a marker."""
    for ident, words in quotes:
        if ident in hidden and words:
            text = text.replace(words, WITHHELD)
    return text


def headline(report: SignalReport) -> str:
    """One sentence stating the direction and the two observations that set it."""
    direction = {
        Trajectory.IMPROVING: "Improving",
        Trajectory.DECLINING: "Declining",
        Trajectory.STEADY: "Steady",
    }[report.trajectory]

    leading = sorted(report.signals, key=lambda signal: -signal.strength)[:2]
    if not leading:
        return f"{direction}: nothing has changed either way in the last {WINDOW_DAYS} days."
    reasons = "; ".join(signal.rationale for signal in leading)
    return f"{direction}: {reasons}."


def _delta_strength(recent: float, prior: float, *, cap: float = 4.0) -> float:
    """How big a change is, on 0-1, without dividing by zero.

    A first occurrence (prior of nothing) counts as a real change but not a maximal one —
    one problem after a quiet fortnight matters, but not as much as four after one.
    """
    if recent <= prior:
        return 0.0
    growth = (recent - prior) / max(1.0, prior)
    return round(min(1.0, growth / cap + 0.25 * min(1.0, recent / 3)), 3)


def _in_window(moment: datetime, *, now: datetime, start_days: float, end_days: float) -> bool:
    age = days_between(ensure_utc(moment), now)
    return start_days <= age < end_days


def compute(
    *,
    memories: Sequence[MemoryView],
    activity: ActivityWindow | None = None,
    goals: Sequence[GoalSnapshot] = (),
    health_score: float = 70.0,
    baseline: Baseline | None = None,
    now: datetime | None = None,
) -> SignalReport:
    """Derive the forecast for one customer."""
    now = now or utcnow()
    activity = activity or ActivityWindow()
    signals: list[Signal] = []
    measurements: dict[str, float] = {}

    def add(
        key: str,
        label: str,
        direction: SignalDirection,
        strength: float,
        horizon_days: int,
        rationale: str,
        memory_ids: Sequence[str] = (),
        observed: float = 0.0,
        quotes: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if strength <= 0:
            return
        signals.append(
            Signal(
                key=key,
                label=label,
                direction=direction,
                strength=min(1.0, strength),
                horizon_days=horizon_days,
                rationale=rationale,
                memory_ids=tuple(memory_ids),
                observed=observed,
                quotes=quotes,
            )
        )

    def recent(items: Sequence[MemoryView]) -> list[MemoryView]:
        return [m for m in items if _in_window(m.first_seen_at, now=now, start_days=0, end_days=WINDOW_DAYS)]

    def prior(items: Sequence[MemoryView]) -> list[MemoryView]:
        return [
            m
            for m in items
            if _in_window(m.first_seen_at, now=now, start_days=WINDOW_DAYS, end_days=WINDOW_DAYS * 2)
        ]

    # ------------------------------------------------------------------ problems
    problems = [m for m in memories if str(m.type) == MemoryType.PROBLEM.value]
    open_problems = [m for m in problems if not m.is_resolved]
    recent_problems, prior_problems = recent(problems), prior(problems)
    measurements["problems_recent"] = len(recent_problems)
    measurements["problems_prior"] = len(prior_problems)

    strength = _delta_strength(len(recent_problems), len(prior_problems))
    if strength and recent_problems:
        add(
            "escalating_problems",
            "problem reports accelerating",
            SignalDirection.RISK,
            strength,
            14,
            f"{len(recent_problems)} problem{'s' if len(recent_problems) != 1 else ''} reported in "
            f"the last {WINDOW_DAYS} days against {len(prior_problems)} in the {WINDOW_DAYS} before",
            [m.id for m in recent_problems],
            observed=len(recent_problems),
        )

    ageing = [
        m
        for m in open_problems
        if days_between(ensure_utc(m.first_seen_at), now) >= AGEING_PROBLEM_DAYS
    ]
    if ageing:
        oldest = max(days_between(ensure_utc(m.first_seen_at), now) for m in ageing)
        measurements["oldest_open_problem_days"] = oldest
        add(
            "ageing_open_problem",
            "an open problem is getting old",
            SignalDirection.RISK,
            min(1.0, oldest / 60),
            21,
            f"a problem opened {ago(oldest)} is still unresolved",
            [m.id for m in ageing],
            observed=oldest,
        )

    repeats = [m for m in open_problems if m.evidence_count >= 3]
    if repeats:
        add(
            "repeat_problem",
            "the same problem keeps coming back",
            SignalDirection.RISK,
            min(1.0, max(m.evidence_count for m in repeats) / 5),
            14,
            f"a problem has now been reported {max(m.evidence_count for m in repeats)} times",
            [m.id for m in repeats],
            observed=max(m.evidence_count for m in repeats),
        )

    # ------------------------------------------------------------- churn language
    # Not summaries: a session or consolidation summary restates what was already said, and
    # counting it would make month-old churn language read as "today" whenever one is written.
    churn_memories = [
        m
        for m in memories
        if str(m.type) != MemoryType.SUMMARY.value
        and (m.churn_risk >= 0.4 or CHURN_LEMMAS & set(content_words(m.content)))
    ]
    recent_churn = recent(churn_memories) or [
        m for m in churn_memories if days_between(ensure_utc(m.last_seen_at), now) < WINDOW_DAYS
    ]
    if recent_churn:
        freshest = min(days_between(ensure_utc(m.last_seen_at), now) for m in recent_churn)
        add(
            "churn_language",
            "talking about leaving",
            SignalDirection.RISK,
            min(1.0, 0.6 + 0.1 * len(recent_churn)),
            30,
            f"cancellation or competitor language {_ago(freshest)}",
            [m.id for m in recent_churn],
            observed=len(recent_churn),
        )

    # -------------------------------------------------------------- engagement
    measurements["events_recent"] = activity.recent_events
    measurements["events_prior"] = activity.prior_events
    if activity.prior_events >= 3 and activity.recent_events < activity.prior_events:
        drop = 1 - activity.recent_events / activity.prior_events
        measurements["engagement_drop"] = drop
        if drop >= 0.4:
            add(
                "engagement_decay",
                "engagement falling away",
                SignalDirection.RISK,
                min(1.0, drop),
                30,
                f"activity down {int(drop * 100)}% against the previous {WINDOW_DAYS} days",
                observed=drop,
            )

    if activity.last_event_at is not None:
        quiet_for = days_between(ensure_utc(activity.last_event_at), now)
        measurements["days_since_last_event"] = quiet_for
        if quiet_for >= SILENCE_DAYS:
            add(
                "silence",
                "gone quiet",
                SignalDirection.RISK,
                min(1.0, quiet_for / 90),
                45,
                f"nothing heard for {int(quiet_for)} days",
                observed=quiet_for,
            )

    # ---------------------------------------------------------------- feedback
    feedback = [m for m in memories if str(m.type) == MemoryType.FEEDBACK.value]
    negative = [m for m in feedback if m.importance >= 0.7]
    positive = [m for m in feedback if m.importance < 0.7]
    strength = _delta_strength(len(recent(negative)), len(prior(negative)))
    if strength:
        add(
            "negative_feedback_trend",
            "feedback turning negative",
            SignalDirection.RISK,
            strength,
            30,
            f"{len(recent(negative))} negative piece{'s' if len(recent(negative)) != 1 else ''} of "
            f"feedback in the last {WINDOW_DAYS} days",
            [m.id for m in recent(negative)],
            observed=len(recent(negative)),
        )

    recent_positive = recent(positive)
    if recent_positive and not open_problems:
        add(
            "advocacy",
            "happy and unblocked",
            SignalDirection.OPPORTUNITY,
            min(1.0, 0.5 + 0.2 * len(recent_positive)),
            30,
            f"{len(recent_positive)} positive piece{'s' if len(recent_positive) != 1 else ''} of "
            "feedback and nothing open against them",
            [m.id for m in recent_positive],
            observed=len(recent_positive),
        )

    # ---------------------------------------------------------------- expansion
    expansion_memories = [
        m
        for m in memories
        if str(m.type) in (MemoryType.SUBSCRIPTION.value, MemoryType.INTENT.value, MemoryType.GOAL.value)
        and UPGRADE_LEMMAS & set(content_words(m.content))
        and not (CHURN_LEMMAS & set(content_words(m.content)))
    ]
    # A later downgrade cancels an earlier upgrade: "they upgraded in March" is not an
    # expansion signal for someone who cancelled in April.
    latest_churn = max(
        (ensure_utc(memory.last_seen_at) for memory in churn_memories), default=None
    )
    recent_expansion = [
        memory
        for memory in recent(expansion_memories)
        if latest_churn is None or ensure_utc(memory.last_seen_at) > latest_churn
    ]
    if recent_expansion:
        add(
            "expansion_intent",
            "asking about more",
            SignalDirection.OPPORTUNITY,
            min(1.0, 0.6 + 0.2 * len(recent_expansion)),
            30,
            "upgrade or expansion language in the last fortnight",
            [m.id for m in recent_expansion],
            observed=len(recent_expansion),
        )

    behaviours = [m for m in memories if str(m.type) == MemoryType.BEHAVIOR.value]
    strength = _delta_strength(len(recent(behaviours)), len(prior(behaviours)))
    if strength:
        add(
            "adoption_growth",
            "using more of the product",
            SignalDirection.OPPORTUNITY,
            strength,
            45,
            f"{len(recent(behaviours))} new usage signal"
            f"{'s' if len(recent(behaviours)) != 1 else ''} in the last {WINDOW_DAYS} days",
            [m.id for m in recent(behaviours)],
            observed=len(recent(behaviours)),
        )

    # -------------------------------------------------------------------- goals
    live_goals = [goal for goal in goals if goal.status in ("open", "progressing", "stalled")]
    stalled = [
        goal
        for goal in live_goals
        if days_between(ensure_utc(goal.last_signal_at), now) >= STALE_GOAL_DAYS
    ]
    if stalled:
        oldest = max(days_between(ensure_utc(goal.last_signal_at), now) for goal in stalled)
        add(
            "goal_stalled",
            "a stated goal has stalled",
            SignalDirection.RISK,
            min(1.0, oldest / 90),
            45,
            f"no progress on “{_clip(stalled[0].statement)}” for {int(oldest)} days",
            observed=len(stalled),
            quotes=((stalled[0].id, _clip(stalled[0].statement)),),
        )

    achieved = [goal for goal in goals if goal.status == "achieved"]
    if achieved:
        add(
            "goal_achieved",
            "hit what they set out to do",
            SignalDirection.OPPORTUNITY,
            min(1.0, 0.5 + 0.2 * len(achieved)),
            30,
            f"reached the goal “{_clip(achieved[0].statement)}”",
            observed=len(achieved),
            quotes=((achieved[0].id, _clip(achieved[0].statement)),),
        )

    # ------------------------------------------------------------ health deltas
    health_delta = 0.0
    if baseline is not None:
        health_delta = health_score - baseline.health_score
        measurements["health_delta"] = health_delta
        measurements["baseline_age_days"] = days_between(ensure_utc(baseline.captured_at), now)
        if health_delta <= -TRAJECTORY_HEALTH_DELTA:
            add(
                "health_slide",
                "health falling",
                SignalDirection.RISK,
                min(1.0, abs(health_delta) / 30),
                30,
                f"health down {abs(health_delta):.0f} points since "
                f"{int(measurements['baseline_age_days'])} days ago",
                observed=health_delta,
            )
        elif health_delta >= TRAJECTORY_HEALTH_DELTA:
            add(
                "health_climb",
                "health rising",
                SignalDirection.OPPORTUNITY,
                min(1.0, health_delta / 30),
                30,
                f"health up {health_delta:.0f} points since "
                f"{int(measurements['baseline_age_days'])} days ago",
                observed=health_delta,
            )

    # ------------------------------------------------------------------- blend
    risk_pressure = min(
        1.0,
        sum(signal.strength * RISK_WEIGHTS.get(signal.key, 0.5) for signal in signals
            if signal.direction == SignalDirection.RISK)
        / _RISK_NORMALISER,
    )
    opportunity_pressure = min(
        1.0,
        sum(signal.strength * OPPORTUNITY_WEIGHTS.get(signal.key, 0.5) for signal in signals
            if signal.direction == SignalDirection.OPPORTUNITY)
        / _OPPORTUNITY_NORMALISER,
    )
    measurements["risk_pressure"] = risk_pressure
    measurements["opportunity_pressure"] = opportunity_pressure

    # Health already prices in what has happened, so signals *escalate* that reading rather
    # than being averaged with it — averaging let a customer with three new problems score
    # lower than their health alone implied. Positive signals can pull it back a little,
    # but never as far as the negative ones push.
    base_risk = max(0.0, min(1.0, 1 - health_score / 100))
    churn_risk = round(
        max(
            0.0,
            min(
                1.0,
                base_risk
                + (1 - base_risk) * 0.6 * risk_pressure
                - 0.15 * opportunity_pressure,
            ),
        ),
        3,
    )
    # An unresolved problem caps how much of an opportunity this can be: nobody expands
    # while something of theirs is broken.
    expansion_score = round(
        max(0.0, opportunity_pressure * (1 - 0.5 * min(1.0, len(open_problems) / 2))), 3
    )

    # Direction, in order of how much the evidence is worth:
    #   1. a measured move against a stored reading beats any amount of inference;
    #   2. one strong signal with nothing pulling the other way sets the direction — a
    #      customer who started talking about cancelling this week is not "steady";
    #   3. otherwise, an accumulation of weaker signals has to clear a margin.
    strongest_risk = max((s.strength for s in signals if s.direction == SignalDirection.RISK), default=0.0)
    strongest_opportunity = max(
        (s.strength for s in signals if s.direction == SignalDirection.OPPORTUNITY), default=0.0
    )

    if health_delta <= -TRAJECTORY_HEALTH_DELTA:
        trajectory = Trajectory.DECLINING
    elif health_delta >= TRAJECTORY_HEALTH_DELTA:
        trajectory = Trajectory.IMPROVING
    elif strongest_risk >= DECISIVE_SIGNAL and risk_pressure > opportunity_pressure:
        trajectory = Trajectory.DECLINING
    elif strongest_opportunity >= DECISIVE_SIGNAL and opportunity_pressure > risk_pressure:
        trajectory = Trajectory.IMPROVING
    elif risk_pressure - opportunity_pressure >= TRAJECTORY_PRESSURE_DELTA:
        trajectory = Trajectory.DECLINING
    elif opportunity_pressure - risk_pressure >= TRAJECTORY_PRESSURE_DELTA:
        trajectory = Trajectory.IMPROVING
    else:
        trajectory = Trajectory.STEADY

    # How much this forecast is worth: a reading from two memories is a guess.
    evidence = len(memories) / 12 * 0.6 + (activity.recent_events + activity.prior_events) / 20 * 0.4
    confidence = round(max(0.1, min(0.95, evidence)), 3)

    return SignalReport(
        trajectory=trajectory,
        churn_risk=churn_risk,
        expansion_score=expansion_score,
        confidence=confidence,
        signals=sorted(signals, key=lambda signal: -signal.strength),
        measurements=measurements,
        computed_at=now,
    )


def ago(days: float) -> str:
    """"today", "yesterday", "12 days ago" — never "1 days ago"."""
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


_ago = ago


def _clip(text: str, limit: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
