"""Ranking retrieved memories.

Vector similarity alone puts a stale, low-confidence memory above a current, repeatedly
confirmed one. The final score blends similarity, importance, confidence, recency and
relationship relevance; the weights are configurable per project.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from common.settings import Settings, get_settings
from memory_engine.schemas import ScoredMemory
from memory_engine.temporal.decay import recency_score


@dataclass(slots=True, frozen=True)
class RankingWeights:
    similarity: float = 0.35
    importance: float = 0.20
    confidence: float = 0.20
    recency: float = 0.15
    relationship: float = 0.10

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RankingWeights:
        settings = settings or get_settings()
        return cls(
            similarity=settings.rank_weight_similarity,
            importance=settings.rank_weight_importance,
            confidence=settings.rank_weight_confidence,
            recency=settings.rank_weight_recency,
            relationship=settings.rank_weight_relationship,
        )

    @classmethod
    def from_mapping(
        cls, mapping: dict[str, float] | None, fallback: RankingWeights | None = None
    ) -> RankingWeights:
        base = fallback or cls()
        if not mapping:
            return base
        return cls(
            similarity=float(mapping.get("similarity", base.similarity)),
            importance=float(mapping.get("importance", base.importance)),
            confidence=float(mapping.get("confidence", base.confidence)),
            recency=float(mapping.get("recency", base.recency)),
            relationship=float(mapping.get("relationship", base.relationship)),
        )

    @property
    def total(self) -> float:
        return (
            self.similarity + self.importance + self.confidence + self.recency + self.relationship
        ) or 1.0


# A concept match is evidence the memory is *about* the question, not that it *says* it.
# Discounted so that, at equal strength, the memory that uses the asker's own words ranks
# first — the concept leg adds recall, it does not reorder what lexical matching found.
CONCEPT_WEIGHT = 0.8


class MemoryRanker:
    def __init__(self, weights: RankingWeights | None = None) -> None:
        self.weights = weights or RankingWeights()

    def score(self, candidate: ScoredMemory) -> float:
        memory = candidate.memory
        weights = self.weights
        similarity = max(
            candidate.similarity, candidate.keyword_score, candidate.concept_score * CONCEPT_WEIGHT
        )
        candidate.recency = candidate.recency or recency_score(
            memory.last_seen_at, memory.type
        )
        raw = (
            weights.similarity * similarity
            + weights.importance * float(memory.importance)
            + weights.confidence * float(memory.confidence)
            + weights.recency * candidate.recency
            + weights.relationship * candidate.relationship_relevance
        )
        candidate.score = round(raw / weights.total, 6)
        return candidate.score

    def rank(
        self, candidates: Iterable[ScoredMemory], *, limit: int | None = None
    ) -> list[ScoredMemory]:
        scored = list(candidates)
        for candidate in scored:
            self.score(candidate)
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit] if limit else scored

    @staticmethod
    def diversify(
        candidates: Sequence[ScoredMemory], *, per_type: int = 4
    ) -> list[ScoredMemory]:
        """Avoid returning ten variations of the same problem and nothing else."""
        counts: dict[str, int] = {}
        kept: list[ScoredMemory] = []
        for candidate in candidates:
            key = str(candidate.memory.type)
            if counts.get(key, 0) >= per_type:
                continue
            counts[key] = counts.get(key, 0) + 1
            kept.append(candidate)
        return kept
