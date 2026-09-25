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
from common.time import days_between, ensure_utc
from database.models import Memory
from database.repositories import MemoryRepository
from memory_engine.consolidation.conflict import ConflictOutcome, ConflictSide, resolve
from memory_engine.consolidation.rules import (
    AUTO_MERGE_SIMILARITY,
    DEFAULT_THRESHOLD,
    CandidateSnapshot,
    Decision,
    MemorySnapshot,
    decide,
    shared_entities,
    topic_overlap,
)
from memory_engine.consolidation.similarity import combined_similarity, cosine_similarity
from memory_engine.policy import Policy
from memory_engine.protocols import Embedder
from memory_engine.schemas import ExtractedMemory, NormalizedEvent
from memory_engine.temporal.decay import expiry_for
from nlp.tokenize import content_words, lemmatize

logger = get_logger(__name__)

# How much a "it works now" statement must share with a problem to be about it — the same
# bar the contradiction rule sets for a problem reported as resolved.
RESOLUTION_OVERLAP = 0.2
# "It works now, thanks!" names nothing. It is taken to resolve the one problem reported in
# this many days before it — and only when there is exactly one: with two it could be
# either, and closing the wrong one is worse than closing neither.
BARE_RESOLUTION_DAYS = 14
# A memory stands for every statement it absorbed, not only its newest wording. A new report
# is compared with the latest few of them too, so "the export failed again last night"
# still meets a problem whose text a later message rewrote into a threat to cancel.
EARLIER_STATEMENTS = 4
# Words a resolution uses whatever it resolves: what is left after them is its topic.
_RESOLUTION_WORDS = frozenset(
    lemmatize(word)
    for word in (
        "it", "works", "working", "worked", "work", "now", "again", "thanks", "thank", "thx", "cheers",
        "fixed", "fixing", "fix", "sorted", "sort", "resolved", "resolve", "solved", "solve", "all", "good",
        "great", "fine", "perfect", "much", "quick", "quickly", "finally", "today", "everything", "back",
        "normal", "up", "running", "run", "support", "team", "help", "helping", "appreciate", "awesome",
        "brilliant", "issue", "problem", "seems", "looks", "so", "very", "really", "guys", "you",
    )
)


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
        embedder: Embedder | None = None,
    ) -> None:
        self.repository = repository
        self.similarity_threshold = similarity_threshold
        self.decay_days = decay_days
        self.neighbours = neighbours
        # For comparing with a memory's earlier statements; without one, only the current
        # wording is compared.
        self.embedder = embedder
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

        # 2. "It works now" closes the problem it is about. The extractor types such a
        #    statement a fact — it is not a problem report — so the same-type neighbours
        #    below would never find the problem it resolves.
        if candidate.attributes.get("resolved") and candidate.type != MemoryType.PROBLEM:
            resolution = await self._resolved_problem(candidate=candidate, event=event, vector=vector)
            if resolution is not None:
                return resolution

        # 3. Closest existing memory for this customer.
        rows = await repo.candidates_for_consolidation(
            project_id=event.project_id,
            customer_id=event.customer_id,
            type=candidate.type,
            vector=vector,
            limit=self.neighbours,
        )
        best_memory, best_similarity, earlier = await self._match(candidate.content, vector, rows)

        if best_memory is None:
            return ConsolidationPlan(
                action=ConsolidationAction.CREATE,
                target=None,
                reason="First memory of its kind for this customer.",
                signals={"rule": "no_neighbour"},
            )

        # 4. Pure rule-based decision.
        decision = decide(
            existing=self._snapshot(best_memory),
            candidate=self._candidate_snapshot(candidate, event),
            similarity=best_similarity,
            threshold=self.similarity_threshold,
        )
        signals = dict(decision.signals)
        if earlier is not None:
            # Said so, so the decision can be replayed: it was the earlier wording that matched.
            signals["matched_earlier_statement"] = earlier[:200]
        return ConsolidationPlan(
            action=decision.action,
            target=best_memory,
            reason=decision.reason,
            similarity=best_similarity,
            signals=signals,
            decision=decision,
        )

    async def _resolved_problem(
        self, *, candidate: ExtractedMemory, event: NormalizedEvent, vector: list[float] | None
    ) -> ConsolidationPlan | None:
        """The open problem a resolution statement is about, as a conflict to resolve.

        Only a problem last reported *before* the statement: one reported again afterwards
        is still happening, whatever an earlier "fixed" said.
        """
        rows = await self.repository.candidates_for_consolidation(
            project_id=event.project_id,
            customer_id=event.customer_id,
            type=MemoryType.PROBLEM,
            vector=vector,
            limit=self.neighbours,
        )
        best: tuple[Memory, float, float, list[str]] | None = None
        for memory, vector_similarity in rows:
            if memory.status != MemoryStatus.ACTIVE or (memory.meta or {}).get("resolved"):
                continue
            if ensure_utc(memory.last_seen_at) > ensure_utc(event.occurred_at):
                continue
            overlap = topic_overlap(memory.content, candidate.content)
            common = shared_entities(list((memory.meta or {}).get("entity_names") or []), list(candidate.entity_names))
            if overlap < RESOLUTION_OVERLAP and not common:
                continue
            score = combined_similarity(vector_similarity, memory.content, candidate.content)
            if best is None or score > best[1]:
                best = (memory, score, overlap, common)
        rule = "problem_resolved"
        if best is None and not self._topic_of(candidate.content):
            recent = await self._recently_reported(event)
            if len(recent) == 1:
                best, rule = (recent[0], 0.0, 0.0, []), "problem_resolved_only_open"
        if best is None:
            return None
        memory, similarity, overlap, common = best
        reason = (
            "A later statement reports this problem resolved."
            if rule == "problem_resolved"
            else "A later statement reports a fix without naming it, and this was the only problem reported recently."
        )
        signals = {"rule": rule, "lexical_overlap": round(overlap, 4), "shared_entities": common}
        return ConsolidationPlan(
            action=ConsolidationAction.CONFLICT,
            target=memory,
            reason=reason,
            similarity=similarity,
            signals=signals,
            decision=Decision(
                action=ConsolidationAction.CONFLICT,
                content=candidate.content,
                confidence=candidate.confidence,
                reason=reason,
                similarity=similarity,
                signals=signals,
            ),
        )

    @staticmethod
    def _topic_of(content: str) -> list[str]:
        return [word for word in content_words(content) if word not in _RESOLUTION_WORDS]

    async def _recently_reported(self, event: NormalizedEvent) -> list[Memory]:
        """Open problems last reported shortly before ``event`` — the candidates a
        resolution that names nothing could be about."""
        problems, _ = await self.repository.list(
            project_id=event.project_id,
            customer_id=event.customer_id,
            type=MemoryType.PROBLEM,
            status=MemoryStatus.ACTIVE,
            limit=20,
        )
        occurred = ensure_utc(event.occurred_at)
        return [
            memory
            for memory in problems
            if not (memory.meta or {}).get("resolved")
            and ensure_utc(memory.last_seen_at) <= occurred
            and days_between(memory.last_seen_at, occurred) <= BARE_RESOLUTION_DAYS
        ]

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
        if (
            candidate.attributes.get("resolved")
            and str(existing.type) == MemoryType.PROBLEM.value
            and ensure_utc(event.occurred_at) >= ensure_utc(existing.last_seen_at)
        ):
            # A fix reported after the last report closes the problem however often it was
            # reported: frequency is evidence that it happened, not that it still does.
            outcome = ConflictOutcome(
                winner="candidate",
                candidate_score=1.0,
                existing_score=0.0,
                factors={"resolution": {"reported_after_last_occurrence": 1.0}},
            )
        else:
            outcome = self._score(existing, candidate, event)
        signals = {
            **decision.signals,
            "conflict_scores": {
                "candidate": outcome.candidate_score,
                "existing": outcome.existing_score,
            },
            "conflict_factors": outcome.factors,
        }
        return await self._settle(decision, existing, candidate, event, importance, outcome, signals)

    @staticmethod
    def _score(existing: Memory, candidate: ExtractedMemory, event: NormalizedEvent) -> ConflictOutcome:
        return resolve(
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

    async def _settle(
        self,
        decision: Decision,
        existing: Memory,
        candidate: ExtractedMemory,
        event: NormalizedEvent,
        importance: float,
        outcome: ConflictOutcome,
        signals: dict[str, Any],
    ) -> ConsolidationOutcome:
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

    async def _match(
        self, content: str, vector: list[float] | None, rows: list[tuple[Memory, float]]
    ) -> tuple[Memory | None, float, str | None]:
        """The closest active neighbour, its score, and the earlier statement of it that
        matched when that scored higher than its current wording."""
        earlier = await self._earlier_statements(rows) if self.embedder is not None and vector else []
        earlier_vectors = (await self.embedder.embed([text for _, text in earlier])).vectors if earlier else []  # type: ignore[union-attr]
        best: Memory | None = None
        best_score = 0.0
        best_via: str | None = None
        for memory, vector_similarity in rows:
            if memory.status != MemoryStatus.ACTIVE:
                continue
            score = combined_similarity(vector_similarity, memory.content, content)
            via: str | None = None
            for (memory_id, statement), statement_vector in zip(earlier, earlier_vectors, strict=True):
                if memory_id != memory.id:
                    continue
                earlier_score = combined_similarity(cosine_similarity(vector or [], statement_vector), statement, content)
                if earlier_score > score:
                    score, via = earlier_score, statement
            if score > best_score:
                best, best_score, best_via = memory, score, via
        return best, best_score, best_via

    async def _earlier_statements(self, rows: list[tuple[Memory, float]]) -> list[tuple[str, str]]:
        """(memory id, statement) for the latest few wordings each neighbour replaced."""
        active = [memory for memory, _ in rows if memory.status == MemoryStatus.ACTIVE]
        if not active:
            return []
        versions = await self.repository.versions_for([memory.id for memory in active])
        found: list[tuple[str, str]] = []
        for memory in active:
            seen = {memory.content}
            kept = 0
            for version in reversed(versions.get(memory.id, [])):  # newest first
                for statement in (version.new_content, version.previous_content):
                    if not statement or statement in seen or kept >= EARLIER_STATEMENTS:
                        continue
                    seen.add(statement)
                    found.append((memory.id, statement))
                    kept += 1
        return found

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
