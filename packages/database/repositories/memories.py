"""Memory persistence plus the five retrieval strategies used by the retrieval engine.

Semantic search alone is not enough (a vector index has no notion of "two weeks ago" or
"only problems"), so keyword, temporal, entity and relationship lookups live here too and
are blended by the ranking engine.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Float, Text, and_, cast, func, select, update
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import aliased

from common.enums import EntityType, MemorySource, MemoryStatus, MemoryType, Sensitivity
from common.ids import new_id
from common.text import content_hash, normalize
from common.time import utcnow
from database.access import UNRESTRICTED, current_access, hidden_condition, visible_conditions
from database.models import Embedding, Entity, Memory, MemoryEntity, MemoryVersion
from database.repositories.base import BaseRepository
from nlp.concepts import concepts_for


class MemoryRepository(BaseRepository):
    """All memory SQL, including the five retrieval strategies.

    Clearance is a property of the repository rather than an argument to each method. Every
    read funnels through :meth:`_active` or :meth:`list`, so an uncleared repository cannot
    return a restricted memory by any route — including a strategy added later that nobody
    remembered to pass a flag to. That is the point: a leak here would be invisible,
    because it would surface as a *composed answer* citing something the reader may not see.

    Passing ``cleared`` at all — ``True`` or ``False`` — makes a *reader-bound* repository,
    which also honours the request's agent profile (:mod:`database.access`): a profile that
    may not read ``feedback`` gets no feedback from any read here. Omitting it makes a
    system repository that sees everything, for the pipeline and for aggregates.
    """

    def __init__(self, session: Any, *, cleared: bool | None = None) -> None:
        super().__init__(session)
        self.reader = cleared is not None
        self.cleared = True if cleared is None else cleared
        access = current_access() if self.reader else UNRESTRICTED
        self.readable_types: frozenset[str] | None = access.readable_types

    @property
    def sees_everything(self) -> bool:
        return self.cleared and self.readable_types is None

    def can_see(self, memory: Any) -> bool:
        """The same rule as the SQL filters, for a row that was found some other way."""
        if not self.cleared and str(memory.sensitivity) == Sensitivity.RESTRICTED.value:
            return False
        return self.readable_types is None or str(memory.type) in self.readable_types

    def _visible(self, *, cleared: bool | None = None) -> list[Any]:
        return visible_conditions(
            cleared=self.cleared if cleared is None else cleared,
            readable_types=self.readable_types,
        )

    # ---------------------------------------------------------------- writes

    async def create(
        self,
        *,
        project_id: str,
        customer_id: str,
        type: MemoryType,
        content: str,
        importance: float,
        confidence: float,
        source: MemorySource = MemorySource.EVENT,
        source_event_ids: list[str] | None = None,
        first_seen_at: datetime | None = None,
        last_seen_at: datetime | None = None,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
    ) -> Memory:
        now = utcnow()
        memory = Memory(
            id=new_id("mem"),
            project_id=project_id,
            customer_id=customer_id,
            type=type,
            content=normalize(content),
            content_hash=content_hash(content),
            concepts=concepts_for(content),
            importance=importance,
            confidence=confidence,
            status=MemoryStatus.ACTIVE,
            source=source,
            sensitivity=sensitivity,
            source_event_ids=source_event_ids or [],
            evidence_count=max(1, len(source_event_ids or [])),
            meta=metadata or {},
            first_seen_at=first_seen_at or now,
            last_seen_at=last_seen_at or now,
            expires_at=expires_at,
        )
        self.session.add(memory)
        await self.session.flush()
        await self.add_version(
            memory_id=memory.id,
            previous_content=None,
            new_content=memory.content,
            reason="created",
            source_event_id=(source_event_ids or [None])[0],
            new_importance=importance,
            new_confidence=confidence,
        )
        return memory

    async def add_version(
        self,
        *,
        memory_id: str,
        previous_content: str | None,
        new_content: str,
        reason: str,
        source_event_id: str | None = None,
        previous_importance: float | None = None,
        new_importance: float | None = None,
        previous_confidence: float | None = None,
        new_confidence: float | None = None,
    ) -> MemoryVersion:
        version = MemoryVersion(
            id=new_id("mvr"),
            memory_id=memory_id,
            previous_content=previous_content,
            new_content=new_content,
            reason=reason,
            source_event_id=source_event_id,
            previous_importance=previous_importance,
            new_importance=new_importance,
            previous_confidence=previous_confidence,
            new_confidence=new_confidence,
            created_at=utcnow(),
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def apply_update(
        self,
        memory: Memory,
        *,
        content: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        status: MemoryStatus | None = None,
        expires_at: datetime | None = None,
        source_event_id: str | None = None,
        last_seen_at: datetime | None = None,
        reason: str = "updated",
    ) -> Memory:
        """Update a memory while preserving the previous version."""
        previous_content = memory.content
        previous_importance = memory.importance
        previous_confidence = memory.confidence

        if content is not None and normalize(content) != previous_content:
            memory.content = normalize(content)
            memory.content_hash = content_hash(content)
            # Concepts follow the words: a rewritten memory can be about something new.
            memory.concepts = concepts_for(content)
        if importance is not None:
            memory.importance = max(0.0, min(1.0, importance))
        if confidence is not None:
            memory.confidence = max(0.0, min(1.0, confidence))
        if status is not None:
            memory.status = status
        if expires_at is not None:
            memory.expires_at = expires_at
        if source_event_id and source_event_id not in memory.source_event_ids:
            memory.source_event_ids = [*memory.source_event_ids, source_event_id]
            memory.evidence_count = len(memory.source_event_ids)
        memory.last_seen_at = last_seen_at or utcnow()

        await self.session.flush()
        await self.add_version(
            memory_id=memory.id,
            previous_content=previous_content,
            new_content=memory.content,
            reason=reason,
            source_event_id=source_event_id,
            previous_importance=previous_importance,
            new_importance=memory.importance,
            previous_confidence=previous_confidence,
            new_confidence=memory.confidence,
        )
        return memory

    async def supersede(self, memory: Memory, *, superseded_by: str, reason: str) -> None:
        memory.status = MemoryStatus.SUPERSEDED
        memory.superseded_by = superseded_by
        await self.session.flush()
        await self.add_version(
            memory_id=memory.id,
            previous_content=memory.content,
            new_content=memory.content,
            reason=reason,
        )

    async def link_entity(self, memory_id: str, entity_id: str) -> None:
        exists = await self.session.scalar(
            select(func.count())
            .select_from(MemoryEntity)
            .where(MemoryEntity.memory_id == memory_id, MemoryEntity.entity_id == entity_id)
        )
        if exists:
            return
        self.session.add(
            MemoryEntity(
                id=new_id("mel"),
                memory_id=memory_id,
                entity_id=entity_id,
                created_at=utcnow(),
            )
        )
        await self.session.flush()

    async def embed(self, memory: Memory, embedder: Any) -> None:
        """Embed a memory's current content with ``embedder``.

        Every write path that creates or rewords a memory calls this. A memory without an
        embedding is invisible to the semantic leg of retrieval — and until this existed,
        memories written through the API, human corrections and agent session summaries
        all were.
        """
        vector = await embedder.embed_one(memory.content)
        await self.upsert_embedding(
            project_id=memory.project_id, memory_id=memory.id, vector=vector, model=embedder.model
        )

    async def upsert_embedding(
        self, *, project_id: str, memory_id: str, vector: list[float], model: str
    ) -> Embedding:
        result = await self.session.execute(
            select(Embedding).where(Embedding.memory_id == memory_id, Embedding.model == model)
        )
        embedding = result.scalar_one_or_none()
        if embedding is None:
            embedding = Embedding(
                id=new_id("emb"),
                project_id=project_id,
                memory_id=memory_id,
                embedding=vector,
                model=model,
                dimensions=len(vector),
                created_at=utcnow(),
            )
            self.session.add(embedding)
        else:
            embedding.embedding = vector
            embedding.dimensions = len(vector)
        await self.session.flush()
        return embedding

    # ----------------------------------------------------------------- reads

    async def get(self, memory_id: str, project_id: str) -> Memory | None:
        conditions = [Memory.id == memory_id, Memory.project_id == project_id, *self._visible()]
        result = await self.session.execute(select(Memory).where(*conditions))
        return result.scalar_one_or_none()

    async def get_many(self, memory_ids: Sequence[str], project_id: str) -> list[Memory]:
        if not memory_ids:
            return []
        conditions = [
            Memory.project_id == project_id,
            Memory.id.in_(list(memory_ids)),
            *self._visible(),
        ]
        result = await self.session.execute(select(Memory).where(*conditions))
        return list(result.scalars())

    async def get_by_content_hash(
        self, *, project_id: str, customer_id: str, hash_value: str
    ) -> Memory | None:
        result = await self.session.execute(
            select(Memory).where(
                Memory.project_id == project_id,
                Memory.customer_id == customer_id,
                Memory.content_hash == hash_value,
                Memory.status == MemoryStatus.ACTIVE,
            )
        )
        return result.scalars().first()

    async def list(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        type: MemoryType | None = None,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
        limit: int = 50,
        offset: int = 0,
        cleared: bool | None = None,
    ) -> tuple[list[Memory], int]:
        """``cleared=False`` excludes restricted memories at the query, not afterwards.

        Filtering in SQL rather than in Python keeps the total honest: a reader without
        clearance is told how many memories they can see, not how many exist. Defaults to
        the repository's own clearance; the profile's types always apply.
        """
        conditions = [Memory.project_id == project_id]
        if customer_id:
            conditions.append(Memory.customer_id == customer_id)
        if type:
            conditions.append(Memory.type == type)
        if status:
            conditions.append(Memory.status == status)
        conditions.extend(self._visible(cleared=cleared))
        total = await self.session.scalar(
            select(func.count()).select_from(Memory).where(*conditions)
        )
        result = await self.session.execute(
            select(Memory)
            .where(*conditions)
            .order_by(Memory.importance.desc(), Memory.last_seen_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def top_by_type(
        self, *, project_id: str, customer_id: str, per_type: int = 10
    ) -> dict[str, list[Memory]]:
        """The most important active memories of each type, in one round trip.

        A Customer 360 needs the current subscription, the open problems, the stated
        preferences and the headline facts. Asking for each separately is five queries that
        grow with the number of sections; taking the top 200 overall and sorting them in
        Python is one query that silently loses the answer — a customer's plan is often an
        old, low-importance memory that a global cut-off drops.

        So the ranking is done per type, in the database, with a window function.
        """
        ranked = (
            select(
                Memory,
                func.row_number()
                .over(
                    partition_by=Memory.type,
                    order_by=(Memory.importance.desc(), Memory.last_seen_at.desc()),
                )
                .label("rank"),
            )
            .where(*self._active(project_id, customer_id))
            .subquery()
        )
        entity = aliased(Memory, ranked)
        result = await self.session.execute(
            select(entity).where(ranked.c.rank <= per_type).order_by(ranked.c.rank)
        )
        grouped: dict[str, list[Memory]] = {}
        for memory in result.scalars():
            grouped.setdefault(str(memory.type), []).append(memory)
        return grouped

    async def restricted_ids(self, *, project_id: str, customer_id: str) -> frozenset[str]:
        """Ids of a customer's active restricted memories — for redacting derived views."""
        result = await self.session.execute(
            select(Memory.id).where(
                Memory.project_id == project_id,
                Memory.customer_id == customer_id,
                Memory.status == MemoryStatus.ACTIVE,
                Memory.sensitivity == Sensitivity.RESTRICTED,
            )
        )
        return frozenset(result.scalars())

    async def hidden_among(self, project_id: str, ids: Iterable[str]) -> frozenset[str]:
        """Which of these memory ids this reader may not see, whatever the memory's status.

        What derived text is redacted with — facts, recommendations, signal rationales —
        so a memory hidden from a list is also hidden from anything that quotes it.
        Superseded memories count: a goal can outlive the memory it was born from and
        still quote it. Ids that are not memories are simply not matched.
        """
        wanted = list({ident for ident in ids if ident})
        hidden = hidden_condition(cleared=self.cleared, readable_types=self.readable_types)
        if hidden is None or not wanted:
            return frozenset()
        result = await self.session.execute(
            select(Memory.id).where(Memory.project_id == project_id, Memory.id.in_(wanted), hidden)
        )
        return frozenset(result.scalars())

    async def events_feeding_hidden(
        self, project_id: str, event_ids: Sequence[str], *, customer_ids: Sequence[str] = ()
    ) -> frozenset[str]:
        """Which of these events contributed to a memory this reader may not see.

        A restricted memory's words are in the event that produced it, so an event list
        that showed raw payloads would undo the restriction one hop away.
        """
        hidden = hidden_condition(cleared=self.cleared, readable_types=self.readable_types)
        wanted = list({ident for ident in event_ids if ident})
        if hidden is None or not wanted:
            return frozenset()
        conditions = [
            Memory.project_id == project_id,
            Memory.source_event_ids.has_any(cast(array(wanted), ARRAY(Text))),
            hidden,
        ]
        if customer_ids:
            conditions.append(Memory.customer_id.in_(list(set(customer_ids))))
        result = await self.session.execute(select(Memory.source_event_ids).where(*conditions))
        found = {ident for (ids,) in result for ident in (ids or [])}
        return frozenset(found & set(wanted))

    async def withheld_count(
        self,
        *,
        project_id: str,
        customer_id: str | None = None,
        type: MemoryType | None = None,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
    ) -> int:
        """How many memories the same filters would have matched but clearance or the
        agent profile hid.

        A repository that sees everything answers without a query. Everyone else pays one
        COUNT, which is the price of a list that is honest about being incomplete.
        """
        hidden = hidden_condition(cleared=self.cleared, readable_types=self.readable_types)
        if hidden is None:
            return 0
        conditions = [Memory.project_id == project_id, hidden]
        if customer_id:
            conditions.append(Memory.customer_id == customer_id)
        if type:
            conditions.append(Memory.type == type)
        if status:
            conditions.append(Memory.status == status)
        return int(
            await self.session.scalar(select(func.count()).select_from(Memory).where(*conditions))
            or 0
        )

    async def active_for_customers(
        self, *, project_id: str, customer_ids: Sequence[str], limit: int = 2000
    ) -> dict[str, list[Memory]]:
        """Active memories for many customers in one query (portfolio views)."""
        if not customer_ids:
            return {}
        conditions = [
            Memory.project_id == project_id,
            Memory.customer_id.in_(list(customer_ids)),
            Memory.status == MemoryStatus.ACTIVE,
            *self._visible(),
        ]
        result = await self.session.execute(
            select(Memory).where(*conditions).order_by(Memory.importance.desc()).limit(limit)
        )
        grouped: dict[str, list[Memory]] = {}
        for memory in result.scalars():
            grouped.setdefault(memory.customer_id, []).append(memory)
        return grouped

    async def feature_counts_by_customer(
        self, *, project_id: str, customer_ids: Sequence[str]
    ) -> dict[str, int]:
        """How many distinct features each customer's memories mention."""
        if not customer_ids:
            return {}
        result = await self.session.execute(
            select(Memory.customer_id, func.count(func.distinct(Entity.id)))
            .join(MemoryEntity, MemoryEntity.memory_id == Memory.id)
            .join(Entity, Entity.id == MemoryEntity.entity_id)
            .where(
                Memory.project_id == project_id,
                Memory.customer_id.in_(list(customer_ids)),
                Memory.status == MemoryStatus.ACTIVE,
                Entity.type == EntityType.FEATURE.value,
            )
            .group_by(Memory.customer_id)
        )
        return {customer_id: int(count) for customer_id, count in result}

    async def versions(self, memory_id: str) -> list[MemoryVersion]:
        result = await self.session.execute(
            select(MemoryVersion)
            .where(MemoryVersion.memory_id == memory_id)
            .order_by(MemoryVersion.created_at.desc())
        )
        return list(result.scalars())

    async def versions_for(self, memory_ids: Sequence[str]) -> dict[str, list[MemoryVersion]]:
        """Every version of several memories, oldest first, in one query."""
        if not memory_ids:
            return {}
        result = await self.session.execute(
            select(MemoryVersion)
            .where(MemoryVersion.memory_id.in_(list(memory_ids)))
            .order_by(MemoryVersion.created_at.asc())
        )
        grouped: dict[str, list[MemoryVersion]] = {}
        for version in result.scalars():
            grouped.setdefault(version.memory_id, []).append(version)
        return grouped

    async def entity_ids_for_memories(self, memory_ids: Sequence[str]) -> dict[str, list[str]]:
        if not memory_ids:
            return {}
        result = await self.session.execute(
            select(MemoryEntity.memory_id, MemoryEntity.entity_id).where(
                MemoryEntity.memory_id.in_(list(memory_ids))
            )
        )
        mapping: dict[str, list[str]] = {}
        for memory_id, entity_id in result:
            mapping.setdefault(memory_id, []).append(entity_id)
        return mapping

    # ------------------------------------------------------------- retrieval

    def _active(self, project_id: str, customer_id: str | None) -> list[Any]:
        conditions: list[Any] = [
            Memory.project_id == project_id,
            Memory.status == MemoryStatus.ACTIVE,
        ]
        if customer_id:
            conditions.append(Memory.customer_id == customer_id)
        conditions.extend(self._visible())
        return conditions

    async def search_semantic(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        vector: list[float],
        model: str | None = None,
        limit: int = 30,
        types: Sequence[MemoryType] | None = None,
    ) -> list[tuple[Memory, float]]:
        """Cosine similarity search over memory embeddings."""
        distance = Embedding.embedding.cosine_distance(vector).label("distance")
        conditions = self._active(project_id, customer_id)
        if model:
            conditions.append(Embedding.model == model)
        if types:
            conditions.append(Memory.type.in_([t.value for t in types]))
        result = await self.session.execute(
            select(Memory, distance)
            .join(Embedding, Embedding.memory_id == Memory.id)
            .where(*conditions)
            .order_by(distance)
            .limit(limit)
        )
        # pgvector cosine distance is in [0, 2]; similarity is 1 - distance.
        return [(memory, max(0.0, 1.0 - float(dist))) for memory, dist in result]

    async def search_concepts(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        concepts: Sequence[str],
        limit: int = 30,
        types: Sequence[MemoryType] | None = None,
    ) -> list[Memory]:
        """Memories that share at least one concept with the question.

        An index scan on ``concepts && :concepts``. Candidates are over-fetched by
        importance and scored by overlap in the retriever, where the query's concepts are
        known — scoring in SQL would mean shipping the Dice formula into a query string.
        """
        if not concepts:
            return []
        conditions = self._active(project_id, customer_id)
        conditions.append(Memory.concepts.overlap(list(concepts)))
        if types:
            conditions.append(Memory.type.in_([t.value for t in types]))
        result = await self.session.execute(
            select(Memory)
            .where(*conditions)
            .order_by(Memory.importance.desc(), Memory.last_seen_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def search_keyword(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        query: str,
        limit: int = 30,
    ) -> list[tuple[Memory, float]]:
        """PostgreSQL full-text search, used alongside vectors for exact terms."""
        if not query.strip():
            return []
        tsquery = func.websearch_to_tsquery("english", query)
        tsvector = func.to_tsvector("english", Memory.content)
        rank = cast(func.ts_rank(tsvector, tsquery), Float).label("rank")
        result = await self.session.execute(
            select(Memory, rank)
            .where(*self._active(project_id, customer_id), tsvector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )
        rows = list(result)
        if rows:
            highest = max(float(rank_value) for _, rank_value in rows) or 1.0
            return [(memory, min(1.0, float(rank_value) / highest)) for memory, rank_value in rows]
        # Fall back to a substring match so short/unusual queries still return something.
        pattern = f"%{query.lower().strip()}%"
        fallback = await self.session.execute(
            select(Memory)
            .where(
                *self._active(project_id, customer_id),
                func.lower(Memory.content).like(pattern),
            )
            .order_by(Memory.importance.desc())
            .limit(limit)
        )
        return [(memory, 0.4) for memory in fallback.scalars()]

    async def search_temporal(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 30,
    ) -> list[Memory]:
        conditions = self._active(project_id, customer_id)
        if since:
            conditions.append(Memory.last_seen_at >= since)
        if until:
            conditions.append(Memory.last_seen_at <= until)
        result = await self.session.execute(
            select(Memory).where(*conditions).order_by(Memory.last_seen_at.desc()).limit(limit)
        )
        return list(result.scalars())

    async def search_by_entities(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        entity_names: Sequence[str],
        limit: int = 30,
    ) -> list[Memory]:
        if not entity_names:
            return []
        normalized = [name.strip().lower() for name in entity_names if name.strip()]
        result = await self.session.execute(
            select(Memory)
            .join(MemoryEntity, MemoryEntity.memory_id == Memory.id)
            .join(Entity, Entity.id == MemoryEntity.entity_id)
            .where(
                *self._active(project_id, customer_id),
                Entity.normalized_name.in_(normalized),
            )
            .order_by(Memory.importance.desc(), Memory.last_seen_at.desc())
            .limit(limit)
        )
        return list(result.scalars().unique())

    async def search_by_entity_ids(
        self,
        *,
        project_id: str,
        customer_id: str | None,
        entity_ids: Sequence[str],
        limit: int = 30,
    ) -> list[Memory]:
        if not entity_ids:
            return []
        result = await self.session.execute(
            select(Memory)
            .join(MemoryEntity, MemoryEntity.memory_id == Memory.id)
            .where(
                *self._active(project_id, customer_id),
                MemoryEntity.entity_id.in_(list(entity_ids)),
            )
            .order_by(Memory.importance.desc())
            .limit(limit)
        )
        return list(result.scalars().unique())

    async def top_for_customer(
        self,
        *,
        project_id: str,
        customer_id: str,
        limit: int = 30,
        types: Sequence[MemoryType] | None = None,
    ) -> list[Memory]:
        conditions = self._active(project_id, customer_id)
        if types:
            conditions.append(Memory.type.in_([t.value for t in types]))
        result = await self.session.execute(
            select(Memory)
            .where(*conditions)
            .order_by(Memory.importance.desc(), Memory.last_seen_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def candidates_for_consolidation(
        self,
        *,
        project_id: str,
        customer_id: str,
        type: MemoryType,
        vector: list[float] | None,
        limit: int = 10,
    ) -> list[tuple[Memory, float]]:
        """Existing memories that a new candidate might merge into."""
        if vector is not None:
            distance = Embedding.embedding.cosine_distance(vector).label("distance")
            result = await self.session.execute(
                select(Memory, distance)
                .join(Embedding, Embedding.memory_id == Memory.id)
                # Same-type neighbours only: a problem never consolidates into a fact.
                .where(*self._active(project_id, customer_id), Memory.type == type.value)
                .order_by(distance)
                .limit(limit)
            )
            return [(memory, max(0.0, 1.0 - float(dist))) for memory, dist in result]
        result = await self.session.execute(
            select(Memory)
            .where(*self._active(project_id, customer_id), Memory.type == type.value)
            .order_by(Memory.last_seen_at.desc())
            .limit(limit)
        )
        return [(memory, 0.0) for memory in result.scalars()]

    # ------------------------------------------------------------ maintenance

    async def expire_due(self, *, project_id: str, now: datetime | None = None) -> int:
        now = now or utcnow()
        result = await self.session.execute(
            update(Memory)
            .where(
                Memory.project_id == project_id,
                Memory.status == MemoryStatus.ACTIVE,
                Memory.expires_at.is_not(None),
                Memory.expires_at <= now,
            )
            .values(status=MemoryStatus.EXPIRED)
        )
        return int(result.rowcount or 0)

    async def count(self, project_id: str, status: MemoryStatus | None = MemoryStatus.ACTIVE) -> int:
        conditions = [Memory.project_id == project_id]
        if status:
            conditions.append(Memory.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(Memory).where(*conditions)
        )
        return int(total or 0)

    async def count_for_customer(
        self,
        *,
        project_id: str,
        customer_id: str,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
        exclude_types: Sequence[MemoryType] | None = None,
    ) -> int:
        """How many memories a customer has. Used to detect summary drift cheaply."""
        conditions = [Memory.project_id == project_id, Memory.customer_id == customer_id]
        if status:
            conditions.append(Memory.status == status)
        if exclude_types:
            conditions.append(Memory.type.notin_([str(item) for item in exclude_types]))
        total = await self.session.scalar(
            select(func.count()).select_from(Memory).where(*conditions)
        )
        return int(total or 0)

    async def count_by_type(self, project_id: str) -> dict[str, int]:
        result = await self.session.execute(
            select(Memory.type, func.count())
            .where(Memory.project_id == project_id, Memory.status == MemoryStatus.ACTIVE)
            .group_by(Memory.type)
        )
        return {str(memory_type): int(count) for memory_type, count in result}

    async def delete_for_customer(self, *, project_id: str, customer_id: str) -> int:
        result = await self.session.execute(
            select(Memory.id).where(
                and_(Memory.project_id == project_id, Memory.customer_id == customer_id)
            )
        )
        ids = [row[0] for row in result]
        if not ids:
            return 0
        await self.session.execute(Memory.__table__.delete().where(Memory.id.in_(ids)))
        return len(ids)
