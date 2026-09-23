"""Event → candidate memories and entities, using the deterministic language engine.

There is no model call here. The extractor is a thin, auditable adapter: it hands the
event's text and payload to :mod:`nlp`, validates the result against the engine's schemas,
and applies engine-level policy (importance floors, per-event caps, known-entity reuse).
"""

from __future__ import annotations

from common.enums import EntityType, MemoryType, RelationshipType
from common.logging import get_logger
from memory_engine.schemas import (
    ExtractedEntity,
    ExtractedMemory,
    ExtractedRelationship,
    ExtractionResult,
    NormalizedEvent,
)
from nlp import extract as nlp_extract
from nlp.entities import EntityHit

logger = get_logger(__name__)

MIN_CONTENT_LENGTH = 8
MAX_MEMORIES_PER_EVENT = 6


class MemoryExtractor:
    """Extracts memories from a normalised event."""

    def __init__(self, max_memories: int = MAX_MEMORIES_PER_EVENT) -> None:
        self.max_memories = max_memories

    def extract(
        self,
        event: NormalizedEvent,
        *,
        customer_name: str | None = None,
        known_entities: list[str] | None = None,
    ) -> ExtractionResult:
        # The payload's own text fields are used, not the flattened "key: value" rendering:
        # extraction must see the customer's sentences exactly as they were written.
        output = nlp_extract(
            event_type=event.event_type,
            data=event.data,
            max_memories=self.max_memories,
            customer_label=customer_name or "The customer",
        )

        memories: list[ExtractedMemory] = []
        for candidate in output.memories:
            content = candidate.content.strip()
            if len(content) < MIN_CONTENT_LENGTH:
                continue
            memories.append(
                ExtractedMemory(
                    type=candidate.type,
                    content=content,
                    # A memory cannot matter dramatically more than the event it came from.
                    importance=min(1.0, max(candidate.importance, event.importance * 0.6)),
                    confidence=candidate.confidence,
                    source=candidate.source,
                    rule=candidate.rule,
                    entity_names=[hit.name for hit in candidate.entities],
                    attributes=candidate.attributes,
                )
            )

        entities = [
            ExtractedEntity(
                type=hit.type,
                name=self._canonical_name(hit, known_entities),
                external_id=hit.external_id,
                confidence=hit.confidence,
                rule=hit.rule,
            )
            for hit in output.entities
            if hit.type is not EntityType.CUSTOMER
        ]

        relationships = [
            ExtractedRelationship(
                source=source,
                type=relation if isinstance(relation, RelationshipType) else RelationshipType.RELATED_TO,
                target=target,
                confidence=confidence,
            )
            for source, relation, target, confidence in output.relationships
        ]

        result = ExtractionResult(
            memories=memories,
            entities=entities,
            relationships=relationships,
            stats={
                **output.stats,
                "sentiment": output.sentiment.as_dict() if output.sentiment else None,
                "types": sorted({str(memory.type) for memory in memories}),
            },
        )
        logger.info(
            "memory.extracted",
            event_id=event.event_id,
            memories=len(result.memories),
            entities=len(result.entities),
            templated=result.stats.get("templated"),
        )
        return result

    @staticmethod
    def _canonical_name(hit: EntityHit, known_entities: list[str] | None) -> str:
        """Reuse an existing entity's exact spelling so the graph does not fragment."""
        if not known_entities:
            return hit.name
        lowered = hit.name.lower()
        for known in known_entities:
            if known.lower() == lowered:
                return known
        return hit.name


def summarize_types(result: ExtractionResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for memory in result.memories:
        key = str(memory.type if isinstance(memory.type, MemoryType) else memory.type)
        counts[key] = counts.get(key, 0) + 1
    return counts
