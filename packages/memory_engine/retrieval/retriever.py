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

logger = get_logger(__name__)


@dataclass(slots=True)
class RetrievalResult:
    memories: list[ScoredMemory]
    analysis: QueryAnalysis
    entity_ids: list[str] = field(default_factory=list)
    strategies_used: list[str] = field(default_factory=list)

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

        async def keyword() -> None:
            with observe(retrieval_latency, strategy="keyword"):
                rows = await self.memories.search_keyword(
                    project_id=project_id,
                    customer_id=customer_id,
                    query=query,
                    limit=self.candidate_limit,
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

        ranked = self.ranker.rank(candidates.values())
        ranked = MemoryRanker.diversify(ranked, per_type=max(2, limit // 2))[:limit]

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
