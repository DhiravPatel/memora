"""Customer data export.

"Show me everything you know about me" is a legal requirement in most of the markets this
product will be sold into, and a debugging tool the rest of the time. The bundle contains
the raw events, the derived memories *and their version history*, the graph, the links, the
tracked goals, the agent sessions and the current health and forecast — everything the
system would use to answer a question.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.health_service import HealthService
from app.services.memory_service import MemoryService
from app.services.serializers import (
    customer_out,
    entity_out,
    event_out,
    goal_out,
    memory_out,
    memory_version_out,
    relationship_out,
    session_out,
)
from app.services.signal_service import SignalService
from common.enums import AuditAction
from common.time import utcnow
from database.models import Customer, Project
from database.repositories import (
    AgentSessionRepository,
    AuditRepository,
    EntityRepository,
    EventRepository,
    GoalRepository,
    MemoryLinkRepository,
    MemoryRepository,
    RelationshipRepository,
)

# Bumped when the bundle gains a section, so an importer can tell what it is reading.
EXPORT_VERSION = "1.1"
MAX_EVENTS = 5000
MAX_MEMORIES = 2000
MAX_GOALS = 200
MAX_SESSIONS = 200


class ExportService:
    def __init__(self, session: AsyncSession, *, cleared: bool = True) -> None:
        """``cleared=False`` hides memories the project's policy restricted.

        An export is the whole content of a customer's record, so clearance applies.
        """
        self.session = session
        self.cleared = cleared
        self.events = EventRepository(session)
        self.memories = MemoryRepository(session, cleared=cleared)
        self.entities = EntityRepository(session)
        self.relationships = RelationshipRepository(session)
        self.links = MemoryLinkRepository(session)
        self.goals = GoalRepository(session)
        self.sessions = AgentSessionRepository(session)
        self.audit = AuditRepository(session)

    async def customer_bundle(
        self,
        *,
        project: Project,
        customer: Customer,
        include_versions: bool = True,
        actor_type: str = "api_key",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        events, event_total = await self.events.list(
            project_id=project.id, customer_id=customer.id, limit=MAX_EVENTS
        )
        memories, memory_total = await self.memories.list(
            project_id=project.id, customer_id=customer.id, status=None, limit=MAX_MEMORIES
        )

        memory_payloads: list[dict[str, Any]] = []
        entity_map = await self.memories.entity_ids_for_memories([memory.id for memory in memories])
        for memory in memories:
            payload = memory_out(memory).model_dump(mode="json")
            payload["entity_ids"] = entity_map.get(memory.id, [])
            if include_versions:
                versions = await self.memories.versions(memory.id)
                payload["versions"] = [
                    memory_version_out(version).model_dump(mode="json") for version in versions
                ]
            memory_payloads.append(payload)

        referenced_entity_ids = {
            entity_id for ids in entity_map.values() for entity_id in ids
        }
        entities = await self.entities.get_many(list(referenced_entity_ids), project.id)
        edges = await self.relationships.for_entities(
            project_id=project.id, entity_ids=list(referenced_entity_ids)
        )
        links = await self.links.for_customer(project_id=project.id, customer_id=customer.id)
        goals, _ = await self.goals.list(
            project_id=project.id, customer_id=customer.id, limit=MAX_GOALS
        )
        sessions, _ = await self.sessions.list(
            project_id=project.id, customer_id=customer.id, limit=MAX_SESSIONS
        )
        health = await HealthService(self.session).for_customer(project=project, customer=customer)
        signals = await SignalService(self.session).for_customer(
            project=project, customer=customer
        )
        graph = await MemoryService(self.session).graph(project=project, customer=customer)

        await self.audit.record(
            action=AuditAction.API_ACCESS,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="customer_export",
            resource_id=customer.id,
            metadata={"events": len(events), "memories": len(memories)},
        )

        return {
            "export_version": EXPORT_VERSION,
            "generated_at": utcnow().isoformat(),
            "project": {"id": project.id, "name": project.name},
            "customer": customer_out(customer).model_dump(mode="json"),
            "counts": {
                "events": event_total,
                "events_included": len(events),
                "memories": memory_total,
                "memories_included": len(memories),
                "entities": len(entities),
                "relationships": len(edges),
                "links": len(links),
                "goals": len(goals),
                "agent_sessions": len(sessions),
            },
            "events": [event_out(event).model_dump(mode="json") for event in events],
            "memories": memory_payloads,
            "entities": [entity_out(entity).model_dump(mode="json") for entity in entities],
            "relationships": [
                relationship_out(edge).model_dump(mode="json") for edge in edges
            ],
            "memory_links": [
                {
                    "id": link.id,
                    "source_memory_id": link.source_memory_id,
                    "target_memory_id": link.target_memory_id,
                    "link_type": link.link_type,
                    "confidence": round(float(link.confidence), 3),
                    "rationale": link.rationale,
                }
                for link in links
            ],
            "graph": graph.model_dump(mode="json"),
            "health": health.health.as_dict(),
            "goals": [goal_out(goal).model_dump(mode="json") for goal in goals],
            "agent_sessions": [
                session_out(item).model_dump(mode="json") for item in sessions
            ],
            "signals": signals.as_dict(),
        }
