"""Memory consolidation.

Extraction on its own produces a pile of near-duplicate statements. The consolidator
decides, for each candidate, whether it is the same thing said again, new evidence for
something already known, a contradiction, or genuinely new — and records every change as a
memory version with the signals that produced it.

The decision itself is a pure function in :mod:`memory_engine.consolidation.rules`; this
module is the part that talks to the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.enums import (
    ConsolidationAction,
    MemorySource,
    MemoryStatus,
    MemoryType,
    Sensitivity,
)
from common.logging import get_logger
from common.metrics import memories_created, memories_updated
from common.text import content_hash
from database.models import Memory
from database.repositories import MemoryRepository
from memory_engine.consolidation.conflict import ConflictSide, resolve
from memory_engine.consolidation.rules import (
    AUTO_MERGE_SIMILARITY,
    DEFAULT_THRESHOLD,
    CandidateSnapshot,
    Decision,
    MemorySnapshot,
    decide,
)
from memory_engine.consolidation.similarity import combined_similarity
from memory_engine.policy import Policy
from memory_engine.schemas import ExtractedMemory, NormalizedEvent
from memory_engine.temporal.decay import expiry_for

logger = get_logger(__name__)


@dataclass(slots=True)
class ConsolidationPlan:
    """What consolidation *would* do with a candidate, decided but not yet applied.

    Separating the decision from the write is what lets a dry-run be honest. A preview
    that re-derived the decision would be a second implementation, and a second
    implementation of a rule engine is a second set of rules — it would agree with the real
    pipeline right up until somebody changed one of them.

    ``target`` is the memory that would be merged into, superseded or contradicted, and is
    ``None`` only when the action is to create.
    """

    action: ConsolidationAction
    target: Memory | None
    reason: str
    similarity: float = 0.0
    signals: dict[str, Any] = None  # type: ignore[assignment]
    # Set for the exact-duplicate and no-neighbour shortcuts, which decide without ever
    # consulting the rules. ``_apply`` is not called for those.
    decision: Decision | None = None

    def __post_init__(self) -> None:
        if self.signals is None:
            self.signals = {}

    @property
    def creates(self) -> bool:
        return self.action is ConsolidationAction.CREATE


@dataclass(slots=True)
class ConsolidationOutcome:
    action: ConsolidationAction
    memory: Memory
    created: bool
    reason: str
    similarity: float = 0.0
    signals: dict[str, Any] = None  # type: ignore[assignment]
    # The existing memory this was compared against — the one changed when something was
    # merged, and the nearest miss when a new memory was created instead. Carried out so
    # the recorded outcome can say what the dry run said, rather than something adjacent.
    closest: Memory | None = None

    def __post_init__(self) -> None:
        if self.signals is None:
            self.signals = {}


class MemoryConsolidator:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        similarity_threshold: float = DEFAULT_THRESHOLD,
        decay_days: int = 90,
        neighbours: int = 6,
        policy: Policy | None = None,
    ) -> None:
        self.repository = repository
        self.similarity_threshold = similarity_threshold
        self.decay_days = decay_days
        self.neighbours = neighbours
        # The project's restriction policy, applied as a memory is written. Classifying
        # here rather than on read means the decision is made once, stored, and indexed.
        self.policy = policy or Policy()

    async def plan(
        self,
        *,
        candidate: ExtractedMemory,
        event: NormalizedEvent,
        vector: list[float] | None,
    ) -> ConsolidationPlan:
        """Decide what to do with a candidate. Reads only — nothing is written.

        Used by :meth:`consolidate` and, unchanged, by the dry-run preview.
        """
        repo = self.repository

        # 1. Exact duplicate: no comparison needed, just fresh evidence.
        duplicate = await repo.get_by_content_hash(
            project_id=event.project_id,
            customer_id=event.customer_id,
            hash_value=content_hash(candidate.content),
        )
        if duplicate is not None:
            return ConsolidationPlan(
                action=ConsolidationAction.MERGE,
                target=duplicate,
                reason="Identical statement already recorded.",
                similarity=1.0,
                signals={"rule": "exact_duplicate"},
            )

        # 2. Closest existing memory for this customer.
        rows = await repo.candidates_for_consolidation(
            project_id=event.project_id,
            customer_id=event.customer_id,
            type=candidate.type,
            vector=vector,
            limit=self.neighbours,
        )
        best_memory, best_similarity = self._best_match(candidate.content, rows)

        if best_memory is None:
            return ConsolidationPlan(
                action=ConsolidationAction.CREATE,
                target=None,
                reason="First memory of its kind for this customer.",
                signals={"rule": "no_neighbour"},
            )

        # 3. Pure rule-based decision.
        decision = decide(
            existing=self._snapshot(best_memory),
            candidate=self._candidate_snapshot(candidate, event),
            similarity=best_similarity,
            threshold=self.similarity_threshold,
        )
        return ConsolidationPlan(
            action=decision.action,
            target=best_memory,
            reason=decision.reason,
            similarity=best_similarity,
            signals=dict(decision.signals),
            decision=decision,
        )

    async def consolidate(
        self,
        *,
        candidate: ExtractedMemory,
        event: NormalizedEvent,
        vector: list[float] | None,
        importance: float,
    ) -> ConsolidationOutcome:
        decided = await self.plan(candidate=candidate, event=event, vector=vector)

        if decided.signals.get("rule") == "exact_duplicate":
            duplicate = decided.target
            assert duplicate is not None  # the rule is only reachable with a target
            await self.repository.apply_update(
                duplicate,
                importance=max(duplicate.importance, importance),
                confidence=min(0.99, duplicate.confidence + 0.02),
                source_event_id=event.event_id,
                last_seen_at=event.occurred_at,
                expires_at=expiry_for(duplicate.type, event.occurred_at, self.decay_days),
                reason="repeated_evidence",
            )
            memories_updated.labels(reason="repeated_evidence").inc()
            return ConsolidationOutcome(
                action=decided.action,
                memory=duplicate,
                created=False,
                reason=decided.reason,
                similarity=decided.similarity,
                signals=decided.signals,
                closest=duplicate,
            )

        if decided.decision is None:
            memory = await self._create(candidate, event, importance)
            return ConsolidationOutcome(
                action=ConsolidationAction.CREATE,
                memory=memory,
                created=True,
                reason=decided.reason,
                signals=decided.signals,
                closest=decided.target,
            )

        assert decided.target is not None  # a rules decision always has one
        logger.info(
            "memory.consolidation_decision",
            event_id=event.event_id,
            memory_id=decided.target.id,
            action=decided.decision.action.value,
            rule=decided.signals.get("rule"),
            similarity=round(decided.similarity, 4),
        )
        applied = await self._apply(
            decided.decision, decided.target, candidate, event, importance
        )
        applied.closest = decided.target
        return applied

    # ------------------------------------------------------------------ apply

    async def _apply(
        self,
        decision: Decision,
        existing: Memory,
        candidate: ExtractedMemory,
        event: NormalizedEvent,
        importance: float,
    ) -> ConsolidationOutcome:
        action = decision.action

        if action is ConsolidationAction.IGNORE:
            return ConsolidationOutcome(
                action=action,
                memory=existing,
                created=False,
                reason=decision.reason,
                similarity=decision.similarity,
                signals=decision.signals,
            )

        if action is ConsolidationAction.CREATE:
            memory = await self._create(candidate, event, importance)
            return ConsolidationOutcome(
                action=action,
                memory=memory,
                created=True,
                reason=decision.reason,
                similarity=decision.similarity,
                signals=decision.signals,
            )

        if action is ConsolidationAction.CONFLICT:
            return await self._resolve_conflict(decision, existing, candidate, event, importance)

        # merge / update
        await self.repository.apply_update(
            existing,
            content=decision.content or existing.content,
            importance=max(float(existing.importance), importance),
            confidence=max(float(existing.confidence), decision.confidence),
            source_event_id=event.event_id,
            last_seen_at=event.occurred_at,
            expires_at=expiry_for(existing.type, event.occurred_at, self.decay_days),
            reason=f"consolidation_{decision.signals.get('rule', action.value)}",
        )
        existing.meta = {
            **(existing.meta or {}),
            "last_consolidation": {
                "action": action.value,
                "reason": decision.reason,
                **decision.signals,
            },
        }
        memories_updated.labels(reason=action.value).inc()
        return ConsolidationOutcome(
            action=action,
            memory=existing,
            created=False,
            reason=decision.reason,
            similarity=decision.similarity,
            signals=decision.signals,
        )

    async def _resolve_conflict(
        self,
        decision: Decision,
        existing: Memory,
        candidate: ExtractedMemory,
        event: NormalizedEvent,
        importance: float,
    ) -> ConsolidationOutcome:
        """Score both sides; the loser is superseded, never deleted."""
        outcome = resolve(
            existing=ConflictSide(
                content=existing.content,
                confidence=float(existing.confidence),
                last_seen_at=existing.last_seen_at,
                evidence_count=existing.evidence_count,
                source=str(existing.source),
            ),
            candidate=ConflictSide(
                content=candidate.content,
                confidence=float(candidate.confidence),
                last_seen_at=event.occurred_at,
                evidence_count=1,
                source="event",
            ),
        )
        signals = {
            **decision.signals,
            "conflict_scores": {
                "candidate": outcome.candidate_score,
                "existing": outcome.existing_score,
            },
            "conflict_factors": outcome.factors,
        }

        if not outcome.candidate_wins:
            # Record that the contradiction was seen and rejected: silence would hide it.
            await self.repository.add_version(
                memory_id=existing.id,
                previous_content=existing.content,
                new_content=existing.content,
                reason="conflict_rejected",
                source_event_id=event.event_id,
            )
            logger.info(
                "memory.conflict_rejected",
                memory_id=existing.id,
                event_id=event.event_id,
                scores=signals["conflict_scores"],
            )
            return ConsolidationOutcome(
                action=ConsolidationAction.CONFLICT,
                memory=existing,
                created=False,
                reason="Existing memory remains better supported.",
                similarity=decision.similarity,
                signals=signals,
            )

        replacement = await self._create(
            candidate,
            event,
            importance,
            content=decision.content or candidate.content,
            metadata={"supersedes": existing.id, "conflict": signals},
        )
        await self.repository.supersede(
            existing, superseded_by=replacement.id, reason="conflict_resolution"
        )
        memories_updated.labels(reason="conflict").inc()
        return ConsolidationOutcome(
            action=ConsolidationAction.CONFLICT,
            memory=replacement,
            created=True,
            reason=decision.reason,
            similarity=decision.similarity,
            signals=signals,
        )

    # ------------------------------------------------------------------ utils

    def _best_match(
        self, content: str, rows: list[tuple[Memory, float]]
    ) -> tuple[Memory | None, float]:
        best: Memory | None = None
        best_score = 0.0
        for memory, vector_similarity in rows:
            if memory.status != MemoryStatus.ACTIVE:
                continue
            score = combined_similarity(vector_similarity, memory.content, content)
            if score > best_score:
                best, best_score = memory, score
        return best, best_score

    @staticmethod
    def _snapshot(memory: Memory) -> MemorySnapshot:
        meta = memory.meta or {}
        return MemorySnapshot(
            id=memory.id,
            # The column stores a string; the rules compare typed enums.
            type=MemoryType(str(memory.type)),
            content=memory.content,
            confidence=float(memory.confidence),
            importance=float(memory.importance),
            evidence_count=memory.evidence_count,
            first_seen_at=memory.first_seen_at,
            last_seen_at=memory.last_seen_at,
            source=str(memory.source),
            entities=list(meta.get("entity_names") or []),
            attributes=meta,
        )

    @staticmethod
    def _candidate_snapshot(candidate: ExtractedMemory, event: NormalizedEvent) -> CandidateSnapshot:
        return CandidateSnapshot(
            type=MemoryType(str(candidate.type)),
            content=candidate.content,
            confidence=float(candidate.confidence),
            importance=float(candidate.importance),
            occurred_at=event.occurred_at,
            entities=list(candidate.entity_names),
            attributes=candidate.attributes,
        )

    async def _create(
        self,
        candidate: ExtractedMemory,
        event: NormalizedEvent,
        importance: float,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        body = content or candidate.content
        verdict = self.policy.evaluate(content=body, memory_type=str(candidate.type))
        memory = await self.repository.create(
            project_id=event.project_id,
            customer_id=event.customer_id,
            type=candidate.type,
            content=body,
            importance=importance,
            confidence=candidate.confidence,
            source=MemorySource.EVENT,
            sensitivity=Sensitivity.RESTRICTED if verdict.restricted else Sensitivity.NORMAL,
            source_event_ids=[event.event_id],
            first_seen_at=event.occurred_at,
            last_seen_at=event.occurred_at,
            expires_at=expiry_for(candidate.type, event.occurred_at, self.decay_days),
            metadata={
                "event_type": event.event_type,
                "rule": candidate.rule,
                "extraction_source": candidate.source,
                "entity_names": list(candidate.entity_names),
                **{
                    key: value
                    for key, value in (candidate.attributes or {}).items()
                    if key in ("cues", "resolved", "quoted", "sentiment", "measurements", "channels")
                },
                **({"restricted_by": verdict.reason} if verdict.restricted else {}),
                **(metadata or {}),
            },
        )
        memories_created.labels(type=str(candidate.type)).inc()
        return memory


__all__ = ["AUTO_MERGE_SIMILARITY", "ConsolidationOutcome", "MemoryConsolidator"]
