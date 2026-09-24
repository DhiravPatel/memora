"""Hybrid retrieval.

A query runs semantic, keyword, temporal, entity and relationship lookups;
the union is deduplicated, annotated with why each memory surfaced, and handed to the
ranker. Relying on vector similarity alone loses exact terms, recency and graph context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from common.enums import MemoryType
from common.logging import get_logger
from common.metrics import observe, retrieval_latency
from database.repositories import EntityRepository, MemoryRepository, RelationshipRepository
from memory_engine.protocols import Embedder
from memory_engine.ranking.ranker import MemoryRanker, RankingWeights
from memory_engine.retrieval.query_analysis import QueryAnalysis, analyze
from memory_engine.schemas import ScoredMemory
from nlp.concepts import concepts_for_many, dice
from nlp.tokenize import lemmatize, tokenize

logger = get_logger(__name__)


# How many of the candidates ranked but not returned are kept, for the decision trace
# (§26 4.4): enough to answer "why wasn't X used?", bounded so a trace stays small.
PASSED_OVER_LIMIT = 15


@dataclass(slots=True)
class PassedOver:
    """A candidate that was ranked but not returned, and why."""

    item: ScoredMemory
    # "type_cap" — the result already held enough memories of its type; "below_cut" —
    # it ranked after the last one returned.
    reason: str
    position: int  # its place in the full ranking, 1-based

    def explain(self) -> dict[str, object]:
        return {
            **self.item.explain(),
            "type": str(self.item.memory.type),
            "reason": self.reason,
            "position": self.position,
        }


@dataclass(slots=True)
class RetrievalResult:
    memories: list[ScoredMemory]
    analysis: QueryAnalysis
    entity_ids: list[str] = field(default_factory=list)
    strategies_used: list[str] = field(default_factory=list)
    passed_over: list[PassedOver] = field(default_factory=list)
    candidate_count: int = 0
    limit: int = 0
    per_type: int = 0

    def ids(self) -> list[str]:
        return [item.memory.id for item in self.memories]

    def explain(self) -> list[dict[str, object]]:
        return [item.explain() for item in self.memories]


class MemoryRetriever:
    def __init__(
        self,
        *,
        memories: MemoryRepository,
        entities: EntityRepository,
        relationships: RelationshipRepository,
        embedder: Embedder,
        ranker: MemoryRanker | None = None,
        candidate_limit: int = 50,
    ) -> None:
        self.memories = memories
        self.entities = entities
        self.relationships = relationships
        self.embedder = embedder
        self.ranker = ranker or MemoryRanker(RankingWeights())
        self.candidate_limit = candidate_limit

    async def retrieve(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        query: str,
        limit: int = 10,
        types: Sequence[MemoryType] | None = None,
        include_semantic: bool = True,
        include_concepts: bool = True,
        keyword_any: bool = True,
        learned_synonyms: dict[str, tuple[str, ...]] | None = None,
    ) -> RetrievalResult:
        # The project's own mined vocabulary widens the keyword leg: a question about "the
        # loader" can then reach memories that only ever say "importer".
        analysis = analyze(query, learned_synonyms=learned_synonyms)
        # An explicit `types` argument filters. Types *inferred* from the question only
        # widen recall: asking "why did they downgrade?" must still surface the problems
        # that explain the downgrade, not just subscription memories.
        explicit_types = list(types) if types else None
        candidates: dict[str, ScoredMemory] = {}
        strategies: list[str] = []

        async def semantic() -> None:
            if not include_semantic or not query.strip():
                return
            vector = await self.embedder.embed_one(query)
            with observe(retrieval_latency, strategy="semantic"):
                rows = await self.memories.search_semantic(
                    project_id=project_id,
                    customer_id=customer_id,
                    vector=vector,
                    model=self.embedder.model,
                    limit=self.candidate_limit,
                    types=explicit_types,
                )
            for memory, similarity in rows:
                entry = candidates.setdefault(memory.id, ScoredMemory(memory=memory))
                entry.similarity = max(entry.similarity, similarity)
                entry.strategies.add("semantic")
            strategies.append("semantic")

        async def concept() -> None:
            """Paraphrase recall: memories about what was asked, in whatever words.

            The question is widened with the project's learned vocabulary first, so a
            synonym the project taught ("loader" for "importer") reaches the concept its
            partner belongs to.
            """
            if not include_concepts or not query.strip():
                return
            words = [lemmatize(token) for token in tokenize(query)]
            expansions = [
                synonym
                for word in words
                for synonym in (learned_synonyms or {}).get(word, ())
            ]
            asked = concepts_for_many([query, " ".join(expansions)])
            if not asked:
                return
            with observe(retrieval_latency, strategy="concept"):
                rows = await self.memories.search_concepts(
                    project_id=project_id,
                    customer_id=customer_id,
                    concepts=asked,
                    limit=self.candidate_limit,
                    types=explicit_types,
                )
            for memory in rows:
                have = set(memory.concepts or [])
                score = dice(asked, have)
                if score <= 0:
                    continue
                entry = candidates.setdefault(memory.id, ScoredMemory(memory=memory))
                if score > entry.concept_score:
                    entry.concept_score = score
                    entry.matched_concepts = sorted(have & set(asked))
                entry.strategies.add("concept")
            strategies.append("concept")

        async def keyword() -> None:
            with observe(retrieval_latency, strategy="keyword"):
                rows = await self.memories.search_keyword(
                    project_id=project_id,
                    customer_id=customer_id,
                    query=query,
                    limit=self.candidate_limit,
                    any_word=keyword_any,
                )
            for memory, score in rows:
                entry = candidates.setdefault(memory.id, ScoredMemory(memory=memory))
                entry.keyword_score = max(entry.keyword_score, score)
                entry.strategies.add("keyword")
            strategies.append("keyword")

        async def by_type() -> None:
            """Guarantee the memory types the question is about are in the pool."""
            inferred = explicit_types or analysis.types
            if not inferred or not customer_id:
                return
            with observe(retrieval_latency, strategy="type"):
                rows = await self.memories.top_for_customer(
                    project_id=project_id,
                    customer_id=customer_id,
                    limit=limit * 2,
                    types=inferred,
                )
            for memory in rows:
                entry = candidates.setdefault(memory.id, ScoredMemory(memory=memory))
                entry.strategies.add("type")
            strategies.append("type")

        async def temporal() -> None:
            if analysis.since is None:
                return
            with observe(retrieval_latency, strategy="temporal"):
                rows = await self.memories.search_temporal(
                    project_id=project_id,
                    customer_id=customer_id,
                    since=analysis.since,
                    until=analysis.until,
                    limit=self.candidate_limit,
                )
            for memory in rows:
                entry = candidates.setdefault(memory.id, ScoredMemory(memory=memory))
                entry.strategies.add("temporal")
            strategies.append("temporal")

        async def entity() -> list[str]:
            if not analysis.entity_names:
                return []
            found = await self.entities.find_by_names(
                project_id=project_id, names=analysis.entity_names
            )
            if not found:
                return []
            with observe(retrieval_latency, strategy="entity"):
                rows = await self.memories.search_by_entity_ids(
                    project_id=project_id,
                    customer_id=customer_id,
                    entity_ids=[item.id for item in found],
                    limit=self.candidate_limit,
                )
            for memory in rows:
                entry = candidates.setdefault(memory.id, ScoredMemory(memory=memory))
                entry.strategies.add("entity")
                entry.relationship_relevance = max(entry.relationship_relevance, 0.6)
            strategies.append("entity")
            return [item.id for item in found]

        # The strategies are independent, but they share one AsyncSession, which does not
        # allow concurrent operations - so they run in sequence, cheapest constraint first.
        await semantic()
        await keyword()
        await concept()
        await by_type()
        await temporal()
        entity_ids: list[str] = await entity()

        if entity_ids:
            await self._apply_relationship_relevance(
                project_id=project_id, entity_ids=entity_ids, candidates=candidates
            )
            strategies.append("relationship")

        if not candidates and customer_id:
            # Nothing matched the question: fall back to the customer's top memories so
            # an agent still gets context instead of nothing.
            for memory in await self.memories.top_for_customer(
                project_id=project_id, customer_id=customer_id, limit=limit
            ):
                candidates.setdefault(memory.id, ScoredMemory(memory=memory)).strategies.add(
                    "fallback"
                )
            strategies.append("fallback")

        everything = self.ranker.rank(candidates.values())
        per_type = max(2, limit // 2)
        diverse = MemoryRanker.diversify(everything, per_type=per_type)
        ranked = diverse[:limit]
        passed_over = _passed_over(everything, diverse, ranked)

        logger.info(
            "memory.retrieved",
            project_id=project_id,
            customer_id=customer_id,
            candidates=len(candidates),
            returned=len(ranked),
            strategies=strategies,
        )
        return RetrievalResult(
            memories=ranked,
            analysis=analysis,
            entity_ids=entity_ids,
            strategies_used=strategies,
            passed_over=passed_over,
            candidate_count=len(candidates),
            limit=limit,
            per_type=per_type,
        )

    async def _apply_relationship_relevance(
        self,
        *,
        project_id: str,
        entity_ids: list[str],
        candidates: dict[str, ScoredMemory],
    ) -> None:
        """Boost memories attached to entities one hop from the query's entities."""
        with observe(retrieval_latency, strategy="relationship"):
            edges = await self.relationships.for_entities(
                project_id=project_id, entity_ids=entity_ids
            )
            neighbour_ids = {
                edge.target_entity_id if edge.source_entity_id in entity_ids
                else edge.source_entity_id
                for edge in edges
            } - set(entity_ids)
            if not neighbour_ids:
                return
            links = await self.memories.entity_ids_for_memories(list(candidates.keys()))

        for memory_id, linked in links.items():
            candidate = candidates.get(memory_id)
            if candidate is None:
                continue
            if set(linked) & neighbour_ids:
                candidate.relationship_relevance = max(candidate.relationship_relevance, 0.4)
                candidate.strategies.add("relationship")


def _passed_over(
    everything: Sequence[ScoredMemory], diverse: Sequence[ScoredMemory], returned: Sequence[ScoredMemory]
) -> list[PassedOver]:
    """The best candidates that did not make it, each with the reason, best first."""
    kept = {item.memory.id for item in diverse}
    shown = {item.memory.id for item in returned}
    found: list[PassedOver] = []
    for position, item in enumerate(everything, start=1):
        if item.memory.id in shown:
            continue
        found.append(PassedOver(item, "below_cut" if item.memory.id in kept else "type_cap", position))
        if len(found) >= PASSED_OVER_LIMIT:
            break
    return found
