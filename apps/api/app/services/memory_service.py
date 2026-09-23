"""Reading memories, timelines and the customer memory graph."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.customers import CustomerTimeline, TimelineEntry
from app.schemas.memories import (
    CausalChain,
    CausalChainStep,
    CustomerLinks,
    EntityOut,
    GraphEdge,
    GraphNode,
    MemoryDetail,
    MemoryGraph,
    MemoryLinkOut,
    MemoryOut,
)
from app.services.serializers import (
    customer_out,
    entity_out,
    memory_out,
    memory_version_out,
)
from common.enums import EntityType, MemorySource, MemoryStatus, MemoryType, Sensitivity
from common.errors import NotFoundError
from common.text import content_hash
from database.models import Customer, Project
from database.repositories import (
    CustomerRepository,
    EntityRepository,
    EventRepository,
    MemoryLinkRepository,
    MemoryRepository,
    RelationshipRepository,
)
from memory_engine.policy import classify


class MemoryService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True) -> None:
        """``cleared=False`` hides memories the project's policy restricted.

        Reads memory content, so it is constrained by the caller's clearance.
        """
        self.session = session
        self.cleared = cleared
        self.memories = MemoryRepository(session, cleared=cleared)
        self.events = EventRepository(session)
        self.customers = CustomerRepository(session)
        self.entities = EntityRepository(session)
        self.relationships = RelationshipRepository(session)
        self.links = MemoryLinkRepository(session)

    async def list_for_customer(
        self,
        *,
        project: Project,
        customer: Customer,
        type: MemoryType | None = None,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryOut], int, int]:
        """Returns the page, the total this reader may see, and how many were withheld."""
        memories, total = await self.memories.list(
            project_id=project.id,
            customer_id=customer.id,
            type=type,
            status=status,
            limit=limit,
            offset=offset,
        )
        withheld = await self.memories.withheld_count(
            project_id=project.id, customer_id=customer.id, type=type, status=status
        )
        return [memory_out(memory) for memory in memories], total, withheld

    async def list_for_project(
        self,
        *,
        project: Project,
        customer: Customer | None = None,
        type: MemoryType | None = None,
        status: MemoryStatus | None = MemoryStatus.ACTIVE,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryOut], int, int]:
        """The project-wide list, with the same clearance and the same withheld count."""
        customer_id = customer.id if customer else None
        memories, total = await self.memories.list(
            project_id=project.id,
            customer_id=customer_id,
            type=type,
            status=status,
            limit=limit,
            offset=offset,
        )
        withheld = await self.memories.withheld_count(
            project_id=project.id, customer_id=customer_id, type=type, status=status
        )
        return [memory_out(memory) for memory in memories], total, withheld

    async def detail(self, *, project: Project, memory_id: str) -> MemoryDetail:
        memory = await self.memories.get(memory_id, project.id)
        if memory is None:
            raise NotFoundError("Memory not found.")
        versions = await self.memories.versions(memory.id)
        entity_map = await self.memories.entity_ids_for_memories([memory.id])
        entities = await self.entities.get_many(entity_map.get(memory.id, []), project.id)
        links = await self.links.for_memory(project_id=project.id, memory_id=memory.id)
        hydrated = await self.links.hydrate(project_id=project.id, links=links)
        return MemoryDetail(
            **memory_out(memory).model_dump(),
            versions=[memory_version_out(version) for version in versions],
            entities=[entity_out(entity) for entity in entities],
            links=[_link_out(link, memory.id, hydrated) for link in links],
        )

    async def links_for_customer(
        self, *, project: Project, customer: Customer, link_type: str | None = None
    ) -> CustomerLinks:
        """Every inferred link for a customer, plus the causal chains behind outcomes."""
        links = await self.links.for_customer(
            project_id=project.id, customer_id=customer.id, link_type=link_type
        )
        hydrated = await self.links.hydrate(project_id=project.id, links=links)

        chains: list[CausalChain] = []
        outcome_ids = {link.source_memory_id for link in links if link.link_type == "caused_by"}
        for outcome_id in outcome_ids:
            outcome = hydrated.get(outcome_id)
            if outcome is None:
                continue
            steps = [
                CausalChainStep(
                    memory_id=link.target_memory_id,
                    content=hydrated[link.target_memory_id].content,
                    type=str(hydrated[link.target_memory_id].type),
                    link_type=link.link_type,
                    confidence=round(float(link.confidence), 3),
                    rationale=link.rationale,
                    occurred_at=hydrated[link.target_memory_id].last_seen_at,
                )
                for link in links
                if link.source_memory_id == outcome_id
                and link.link_type == "caused_by"
                and link.target_memory_id in hydrated
            ]
            steps.sort(key=lambda step: step.occurred_at)
            chains.append(
                CausalChain(
                    outcome_memory_id=outcome.id,
                    outcome_content=outcome.content,
                    occurred_at=outcome.last_seen_at,
                    steps=steps,
                )
            )
        chains.sort(key=lambda chain: chain.occurred_at, reverse=True)

        return CustomerLinks(
            customer_id=customer.id,
            links=[_link_out(link, None, hydrated) for link in links],
            chains=chains,
        )

    async def create_manual(
        self,
        *,
        project: Project,
        customer: Customer,
        type: MemoryType,
        content: str,
        importance: float,
        confidence: float,
    ) -> MemoryOut:
        """Operator-authored memory. Recorded with source=manual so it outranks inferences."""
        existing = await self.memories.get_by_content_hash(
            project_id=project.id, customer_id=customer.id, hash_value=content_hash(content)
        )
        if existing is not None:
            return memory_out(existing)
        # The consolidator classifies what it writes; a memory typed in by hand has to be
        # classified on the same rules, or the policy would cover only the event path.
        sensitivity, reason = classify(
            project.settings, content=content, memory_type=str(type)
        )
        memory = await self.memories.create(
            project_id=project.id,
            customer_id=customer.id,
            type=type,
            content=content,
            importance=importance,
            confidence=confidence,
            source=MemorySource.MANUAL,
            sensitivity=Sensitivity(sensitivity),
            metadata={"restricted_by": reason} if reason else None,
        )
        return memory_out(memory)

    async def timeline(
        self, *, project: Project, customer: Customer, limit: int = 100
    ) -> CustomerTimeline:
        events, _ = await self.events.list(
            project_id=project.id, customer_id=customer.id, limit=limit
        )
        memories, _ = await self.memories.list(
            project_id=project.id, customer_id=customer.id, status=None, limit=limit
        )

        entries: list[TimelineEntry] = [
            TimelineEntry(
                kind="event",
                id=event.id,
                title=event.event_type.replace("_", " "),
                detail=_event_detail(event.data or {}),
                occurred_at=event.occurred_at,
                metadata={
                    "status": str(event.status),
                    "importance": event.importance,
                    "data": event.data or {},
                },
            )
            for event in events
        ]
        entries.extend(
            TimelineEntry(
                kind="memory",
                id=memory.id,
                title=str(memory.type),
                detail=memory.content,
                occurred_at=memory.last_seen_at,
                metadata={
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                    "status": str(memory.status),
                    "evidence_count": memory.evidence_count,
                    "source_event_ids": list(memory.source_event_ids or []),
                },
            )
            for memory in memories
        )
        entries.sort(key=lambda entry: entry.occurred_at, reverse=True)
        return CustomerTimeline(customer=customer_out(customer), entries=entries[:limit])

    async def graph(
        self, *, project: Project, customer: Customer, depth: int = 1
    ) -> MemoryGraph:
        """The customer's neighbourhood of the entity graph, ready for React Flow."""
        label = (customer.name or customer.external_id).strip().lower()
        matches = await self.entities.find_by_names(project_id=project.id, names=[label])
        root = next(
            (entity for entity in matches if entity.type == EntityType.CUSTOMER),
            matches[0] if matches else None,
        )
        if root is None:
            return MemoryGraph(nodes=[], edges=[])

        frontier = [root.id]
        seen_entities: dict[str, Any] = {root.id: root}
        edges: dict[str, GraphEdge] = {}

        for _ in range(max(1, depth)):
            relationships = await self.relationships.for_entities(
                project_id=project.id, entity_ids=frontier
            )
            if not relationships:
                break
            neighbour_ids = {
                edge_id
                for relationship in relationships
                for edge_id in (relationship.source_entity_id, relationship.target_entity_id)
            } - set(seen_entities)
            for entity in await self.entities.get_many(list(neighbour_ids), project.id):
                seen_entities[entity.id] = entity
            for relationship in relationships:
                if (
                    relationship.source_entity_id in seen_entities
                    and relationship.target_entity_id in seen_entities
                ):
                    edges[relationship.id] = GraphEdge(
                        id=relationship.id,
                        source=relationship.source_entity_id,
                        target=relationship.target_entity_id,
                        label=str(relationship.relationship_type).replace("_", " "),
                        confidence=round(float(relationship.confidence), 3),
                    )
            frontier = list(neighbour_ids)
            if not frontier:
                break

        nodes = [
            GraphNode(
                id=entity.id,
                label=entity.name,
                type=str(entity.type),
                mention_count=entity.mention_count,
                is_root=entity.id == root.id,
            )
            for entity in seen_entities.values()
        ]
        return MemoryGraph(nodes=nodes, edges=list(edges.values()))

    async def entities_for_project(
        self, *, project: Project, limit: int = 100, offset: int = 0
    ) -> tuple[list[EntityOut], int]:
        entities, total = await self.entities.list(
            project_id=project.id, limit=limit, offset=offset
        )
        return [entity_out(entity) for entity in entities], total


def _link_out(link: Any, memory_id: str | None, hydrated: dict[str, Any]) -> MemoryLinkOut:
    outgoing = memory_id is None or link.source_memory_id == memory_id
    other_id = link.target_memory_id if outgoing else link.source_memory_id
    other = hydrated.get(other_id)
    return MemoryLinkOut(
        id=link.id,
        link_type=link.link_type,
        confidence=round(float(link.confidence), 3),
        rationale=link.rationale,
        direction="outgoing" if outgoing else "incoming",
        other_memory_id=other_id,
        other_content=other.content if other else None,
        other_type=str(other.type) if other else None,
    )


def _event_detail(data: dict[str, Any]) -> str | None:
    for key in ("message", "text", "body", "reason", "feature", "plan", "error"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return None
