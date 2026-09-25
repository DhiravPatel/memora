"""Customer health and churn risk, computed from memory.

A score is only useful if you can argue with it, so this returns the factors that produced
it — each with its own contribution, direction and the memories behind it. Every input is
already in the memory graph: no separate analytics pipeline, no model, no training data.

    score  : 0-100, where 100 is a customer with no negative signals and real engagement
    band   : healthy | watch | at_risk | critical
    factors: what moved the score, and by how much
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.enums import MemoryType
from common.time import days_between, ensure_utc, utcnow
from nlp.answer import MemoryView
from nlp.sentiment import feedback_tone
from nlp.tokenize import content_words, lemmatize

BASELINE = 70.0

BANDS: tuple[tuple[float, str], ...] = (
    (80.0, "healthy"),
    (60.0, "watch"),
    (35.0, "at_risk"),
    (0.0, "critical"),
)

CHURN_LEMMAS = {
    lemmatize(word)
    for word in ("cancel", "cancelled", "cancellation", "downgrade", "downgraded", "churn",
                 "refund", "terminate", "competitor", "switching")
}
UPGRADE_LEMMAS = {lemmatize(word) for word in ("upgrade", "upgraded", "expanded", "renewed")}

# Weights are deliberately small and additive so no single signal can dominate. These are
# the defaults; a project can override any of them (see ``resolve_weights``), because what
# "unhealthy" means is different for a self-serve product and an enterprise contract.
WEIGHTS = {
    "open_problem": -7.0,
    "repeated_problem": -6.0,
    "urgent_problem": -5.0,
    "churn_language": -14.0,
    "downgrade": -16.0,
    "upgrade": 10.0,
    "negative_feedback": -6.0,
    "positive_feedback": 8.0,
    "resolved_problem": 3.0,
    "recent_engagement": 8.0,
    "feature_adoption": 4.0,
    "goal_recorded": 3.0,
    "silence": -10.0,
    "stated_preference": 2.0,
}

RECENT_DAYS = 14
SILENCE_DAYS = 45

# An override outside this range would let one factor swamp the 0-100 scale.
MIN_WEIGHT = -40.0
MAX_WEIGHT = 40.0


def resolve_weights(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    """Merge a project's overrides over the defaults.

    Unknown keys are ignored rather than rejected: this is called on the read path, and a
    stale key left behind by an older version must not stop a score being produced. The
    settings validator is where a bad key is refused, loudly, at write time.
    """
    weights = dict(WEIGHTS)
    for key, value in (overrides or {}).items():
        if key not in WEIGHTS:
            continue
        try:
            weights[key] = max(MIN_WEIGHT, min(MAX_WEIGHT, float(value)))
        except (TypeError, ValueError):
            continue
    return weights


@dataclass(slots=True)
class HealthFactor:
    key: str
    label: str
    contribution: float
    count: int = 0
    memory_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "contribution": round(self.contribution, 2),
            "count": self.count,
            "memory_ids": self.memory_ids[:5],
        }


@dataclass(slots=True)
class HealthScore:
    score: float
    band: str
    churn_risk: float
    factors: list[HealthFactor] = field(default_factory=list)
    computed_at: datetime = field(default_factory=utcnow)
    memories_considered: int = 0
    events_considered: int = 0

    @property
    def is_at_risk(self) -> bool:
        return self.band in ("at_risk", "critical")

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "band": self.band,
            "churn_risk": round(self.churn_risk, 3),
            "factors": [factor.as_dict() for factor in self.factors],
            "computed_at": self.computed_at.isoformat(),
            "memories_considered": self.memories_considered,
            "events_considered": self.events_considered,
            "explanation": explain(self),
        }


def band_for(score: float) -> str:
    for threshold, name in BANDS:
        if score >= threshold:
            return name
    return "critical"


def explain(health: HealthScore) -> str:
    """One sentence a human can read in a dashboard cell."""
    if not health.factors:
        return "No signals recorded yet; the score is the neutral baseline."
    negatives = [factor for factor in health.factors if factor.contribution < 0]
    positives = [factor for factor in health.factors if factor.contribution > 0]
    parts: list[str] = []
    if negatives:
        worst = sorted(negatives, key=lambda factor: factor.contribution)[:2]
        parts.append("pulled down by " + " and ".join(factor.label for factor in worst))
    if positives:
        best = sorted(positives, key=lambda factor: -factor.contribution)[:2]
        parts.append("supported by " + " and ".join(factor.label for factor in best))
    return f"Health {health.score:.0f}/100 ({health.band}), " + "; ".join(parts) + "."


def _recency_weight(last_seen_at: datetime, *, now: datetime) -> float:
    """Recent signals count fully; old ones fade rather than vanish."""
    age = max(0.0, days_between(last_seen_at, now))
    return round(max(0.25, 0.5 ** (age / 60)), 4)


def compute(
    *,
    memories: Sequence[MemoryView],
    event_count: int = 0,
    last_event_at: datetime | None = None,
    distinct_features: int = 0,
    weights: dict[str, float] | None = None,
    now: datetime | None = None,
) -> HealthScore:
    """Score a customer from their active memories and basic activity stats.

    ``weights`` overrides the defaults per project, so a team can decide that, say, churn
    language matters more to them than an open problem does.
    """
    now = now or utcnow()
    factors: list[HealthFactor] = []
    score = BASELINE
    WEIGHTS = resolve_weights(weights)  # noqa: N806 - shadows the module default on purpose

    def add(key: str, label: str, contribution: float, memory_ids: Sequence[str] = (), count: int = 0) -> None:
        nonlocal score
        if not contribution:
            return
        score += contribution
        factors.append(
            HealthFactor(
                key=key,
                label=label,
                contribution=contribution,
                count=count or len(memory_ids),
                memory_ids=list(memory_ids),
            )
        )

    problems = [memory for memory in memories if str(memory.type) == MemoryType.PROBLEM.value]
    open_problems = [memory for memory in problems if not memory.is_resolved]
    resolved_problems = [memory for memory in problems if memory.is_resolved]
    repeated = [memory for memory in open_problems if memory.evidence_count >= 2]
    urgent = [memory for memory in open_problems if memory.urgency >= 0.6]

    if open_problems:
        weighted = sum(_recency_weight(memory.last_seen_at, now=now) for memory in open_problems)
        add(
            "open_problems",
            f"{len(open_problems)} open problem{'s' if len(open_problems) > 1 else ''}",
            WEIGHTS["open_problem"] * min(3.0, weighted),
            [memory.id for memory in open_problems],
        )
    if repeated:
        add(
            "repeated_problems",
            "problems reported more than once",
            WEIGHTS["repeated_problem"] * min(2.0, len(repeated)),
            [memory.id for memory in repeated],
        )
    if urgent:
        add(
            "urgent_problems",
            "urgent language in reports",
            WEIGHTS["urgent_problem"] * min(2.0, len(urgent)),
            [memory.id for memory in urgent],
        )
    if resolved_problems:
        add(
            "resolved_problems",
            "problems that were resolved",
            WEIGHTS["resolved_problem"] * min(3.0, len(resolved_problems)),
            [memory.id for memory in resolved_problems],
        )

    churn_memories = [
        memory
        for memory in memories
        if memory.churn_risk >= 0.4 or CHURN_LEMMAS & set(content_words(memory.content))
    ]
    downgrades = [
        memory
        for memory in churn_memories
        if str(memory.type) in (MemoryType.SUBSCRIPTION.value, MemoryType.INTENT.value)
    ]
    if churn_memories:
        weighted = sum(_recency_weight(memory.last_seen_at, now=now) for memory in churn_memories)
        add(
            "churn_language",
            "memories mentioning leaving or cancelling",
            WEIGHTS["churn_language"] * min(2.0, weighted),
            [memory.id for memory in churn_memories],
        )
    if downgrades:
        add(
            "downgrade",
            "a recorded downgrade or cancellation",
            WEIGHTS["downgrade"] * min(1.5, len(downgrades)),
            [memory.id for memory in downgrades],
        )

    upgrades = [
        memory
        for memory in memories
        if str(memory.type) == MemoryType.SUBSCRIPTION.value
        and UPGRADE_LEMMAS & set(content_words(memory.content))
        and memory not in downgrades
    ]
    if upgrades:
        add("upgrade", "an upgrade or renewal", WEIGHTS["upgrade"], [memory.id for memory in upgrades])

    feedback = [memory for memory in memories if str(memory.type) == MemoryType.FEEDBACK.value]
    # By what was said — a score's band, else its sentiment — never by importance: an NPS
    # promoter's 9 and a furious one-liner can carry the same importance.
    tones = {memory.id: feedback_tone(memory.content, memory.attributes) for memory in feedback}
    negative_feedback = [memory for memory in feedback if tones[memory.id] == "negative"]
    positive_feedback = [memory for memory in feedback if tones[memory.id] == "positive"]
    if negative_feedback:
        add(
            "negative_feedback",
            "negative feedback",
            WEIGHTS["negative_feedback"] * min(2.0, len(negative_feedback)),
            [memory.id for memory in negative_feedback],
        )
    if positive_feedback:
        add(
            "positive_feedback",
            "positive feedback",
            WEIGHTS["positive_feedback"] * min(2.0, len(positive_feedback)),
            [memory.id for memory in positive_feedback],
        )

    goals = [
        memory
        for memory in memories
        if str(memory.type) in (MemoryType.GOAL.value, MemoryType.INTENT.value)
        and memory not in churn_memories
    ]
    if goals:
        add("goals", "stated goals", WEIGHTS["goal_recorded"] * min(2.0, len(goals)),
            [memory.id for memory in goals])

    preferences = [memory for memory in memories if str(memory.type) == MemoryType.PREFERENCE.value]
    if preferences:
        add("preferences", "stated preferences", WEIGHTS["stated_preference"],
            [memory.id for memory in preferences])

    if distinct_features:
        add(
            "feature_adoption",
            f"{distinct_features} feature{'s' if distinct_features != 1 else ''} in use",
            WEIGHTS["feature_adoption"] * min(2.0, distinct_features / 2),
            count=distinct_features,
        )

    if last_event_at is not None:
        age = days_between(ensure_utc(last_event_at), now)
        if age <= RECENT_DAYS:
            add("recent_engagement", "activity in the last two weeks", WEIGHTS["recent_engagement"])
        elif age >= SILENCE_DAYS:
            add("silence", f"no activity for {int(age)} days", WEIGHTS["silence"])
    elif event_count == 0:
        add("no_activity", "no recorded activity", WEIGHTS["silence"] / 2)

    score = max(0.0, min(100.0, score))
    return HealthScore(
        score=score,
        band=band_for(score),
        churn_risk=round(1 - score / 100, 3),
        factors=sorted(factors, key=lambda factor: factor.contribution),
        memories_considered=len(memories),
        events_considered=event_count,
        computed_at=now,
    )
