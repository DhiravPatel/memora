"""Conflict resolution between an existing memory and a contradicting new one.

Nothing is ever silently overwritten: the loser is superseded (and kept), and the
decision is recorded with the factors that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from common.time import days_between, utcnow

# Weights for the factors the README requires: recency, confidence, frequency,
# explicitness and source.
WEIGHTS = {
    "recency": 0.35,
    "confidence": 0.25,
    "frequency": 0.15,
    "explicitness": 0.15,
    "source": 0.10,
}

# Phrases where the customer states a preference directly rather than us inferring it.
_EXPLICIT_MARKERS = (
    "please", "i prefer", "i'd rather", "i would rather", "contact me", "don't", "do not",
    "stop", "instead", "from now on", "always", "never",
)

_SOURCE_TRUST = {
    "manual": 1.0,
    "event": 0.8,
    "consolidation": 0.7,
    "import": 0.6,
}


@dataclass(slots=True)
class ConflictSide:
    content: str
    confidence: float
    last_seen_at: datetime
    evidence_count: int = 1
    source: str = "event"


@dataclass(slots=True)
class ConflictOutcome:
    winner: str  # "candidate" | "existing"
    candidate_score: float
    existing_score: float
    factors: dict[str, dict[str, float]]

    @property
    def candidate_wins(self) -> bool:
        return self.winner == "candidate"


def explicitness(content: str) -> float:
    lowered = content.lower()
    hits = sum(1 for marker in _EXPLICIT_MARKERS if marker in lowered)
    return min(1.0, 0.3 + 0.2 * hits)


def recency_score(last_seen_at: datetime, *, half_life_days: float = 30.0) -> float:
    age_days = max(0.0, days_between(last_seen_at, utcnow()))
    return 0.5 ** (age_days / half_life_days)


def frequency_score(evidence_count: int) -> float:
    # Diminishing returns: the tenth confirmation matters less than the second.
    return min(1.0, 0.3 + 0.7 * (1 - 0.7**max(0, evidence_count - 1)))


def _score(side: ConflictSide) -> tuple[float, dict[str, float]]:
    factors = {
        "recency": recency_score(side.last_seen_at),
        "confidence": max(0.0, min(1.0, side.confidence)),
        "frequency": frequency_score(side.evidence_count),
        "explicitness": explicitness(side.content),
        "source": _SOURCE_TRUST.get(side.source, 0.7),
    }
    total = sum(WEIGHTS[name] * value for name, value in factors.items())
    return round(total, 4), factors


def resolve(existing: ConflictSide, candidate: ConflictSide) -> ConflictOutcome:
    """Decide whether a contradicting new statement should replace the old one."""
    existing_score, existing_factors = _score(existing)
    candidate_score, candidate_factors = _score(candidate)
    # The incumbent keeps its position on a tie: churn in memory is expensive.
    winner = "candidate" if candidate_score > existing_score else "existing"
    return ConflictOutcome(
        winner=winner,
        candidate_score=candidate_score,
        existing_score=existing_score,
        factors={"existing": existing_factors, "candidate": candidate_factors},
    )
