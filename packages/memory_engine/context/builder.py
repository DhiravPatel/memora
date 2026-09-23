"""Context builder.

Converts ranked memories into a compact, AI-ready description of a customer. It enforces
a token budget, removes near-duplicates, and keeps the distinction between what happened
(events), what we know (memories) and how things relate (graph edges).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from common.enums import MemoryType
from common.text import jaccard
from common.tokens import estimate_tokens, truncate_to_tokens
from memory_engine.schemas import ScoredMemory

# Memories whose wording overlaps this much are treated as the same statement.
DEDUPLICATION_THRESHOLD = 0.8

_SECTION_FOR_TYPE: dict[MemoryType, str] = {
    MemoryType.PROBLEM: "active_problems",
    MemoryType.PREFERENCE: "preferences",
    MemoryType.GOAL: "goals",
    MemoryType.INTENT: "goals",
    MemoryType.SUBSCRIPTION: "important_facts",
    MemoryType.FACT: "important_facts",
    MemoryType.RELATIONSHIP: "important_facts",
    MemoryType.FEEDBACK: "feedback",
    MemoryType.BEHAVIOR: "product_usage",
    MemoryType.SUMMARY: "summary",
}

_SECTION_ORDER = (
    "active_problems",
    "important_facts",
    "preferences",
    "goals",
    "feedback",
    "product_usage",
    "summary",
)


@dataclass(slots=True)
class ContextSection:
    name: str
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CustomerContext:
    customer: dict[str, Any]
    sections: dict[str, list[dict[str, Any]]]
    recent_events: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    memory_ids: list[str]
    token_count: int
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer": self.customer,
            "important_facts": [item["content"] for item in self.sections.get("important_facts", [])],
            "active_problems": [item["content"] for item in self.sections.get("active_problems", [])],
            "preferences": [item["content"] for item in self.sections.get("preferences", [])],
            "goals": [item["content"] for item in self.sections.get("goals", [])],
            "feedback": [item["content"] for item in self.sections.get("feedback", [])],
            "product_usage": [item["content"] for item in self.sections.get("product_usage", [])],
            "recent_events": self.recent_events,
            "relationships": self.relationships,
            "memories": [item for section in self.sections.values() for item in section],
            "memory_ids": self.memory_ids,
            "token_count": self.token_count,
            "truncated": self.truncated,
        }

    def to_prompt_text(self) -> str:
        """A plain-text rendering for direct injection into another system's prompt."""
        lines: list[str] = [f"Customer: {self.customer.get('name') or self.customer.get('id')}"]
        for section in _SECTION_ORDER:
            items = self.sections.get(section)
            if not items:
                continue
            lines.append(f"\n{section.replace('_', ' ').title()}:")
            lines.extend(f"- {item['content']}" for item in items)
        if self.recent_events:
            lines.append("\nRecent Events:")
            lines.extend(
                f"- {event['occurred_at']}: {event['event_type']}" for event in self.recent_events
            )
        if self.relationships:
            lines.append("\nRelationships:")
            lines.extend(
                f"- {edge['source']} {edge['type']} {edge['target']}" for edge in self.relationships
            )
        return "\n".join(lines)


class ContextBuilder:
    def __init__(self, *, token_budget: int = 2000, max_per_section: int = 6) -> None:
        self.token_budget = token_budget
        self.max_per_section = max_per_section

    def build(
        self,
        *,
        customer: dict[str, Any],
        memories: Sequence[ScoredMemory],
        recent_events: Sequence[dict[str, Any]] = (),
        relationships: Sequence[dict[str, Any]] = (),
        token_budget: int | None = None,
    ) -> CustomerContext:
        budget = token_budget or self.token_budget
        sections: dict[str, list[dict[str, Any]]] = {}
        memory_ids: list[str] = []
        used_tokens = estimate_tokens(str(customer))
        truncated = False

        for candidate in memories:
            memory = candidate.memory
            section = _SECTION_FOR_TYPE.get(_as_type(memory.type), "important_facts")
            bucket = sections.setdefault(section, [])
            if len(bucket) >= self.max_per_section:
                truncated = True
                continue
            content = memory.content.strip()
            if self._is_duplicate(content, sections):
                continue

            cost = estimate_tokens(content) + 12  # + per-item JSON overhead
            if used_tokens + cost > budget:
                truncated = True
                continue

            used_tokens += cost
            bucket.append(
                {
                    "id": memory.id,
                    "content": content,
                    "type": str(memory.type),
                    "importance": round(float(memory.importance), 3),
                    "confidence": round(float(memory.confidence), 3),
                    "last_seen_at": memory.last_seen_at.isoformat(),
                    "evidence_count": memory.evidence_count,
                    "source_event_ids": list(memory.source_event_ids or []),
                    "score": round(candidate.score, 4),
                    "retrieved_by": sorted(candidate.strategies),
                }
            )
            memory_ids.append(memory.id)

        events: list[dict[str, Any]] = []
        for event in recent_events:
            cost = estimate_tokens(str(event))
            if used_tokens + cost > budget:
                truncated = True
                break
            used_tokens += cost
            events.append(event)

        edges: list[dict[str, Any]] = []
        for edge in relationships:
            cost = estimate_tokens(str(edge))
            if used_tokens + cost > budget:
                truncated = True
                break
            used_tokens += cost
            edges.append(edge)

        ordered = {name: sections[name] for name in _SECTION_ORDER if sections.get(name)}
        return CustomerContext(
            customer=customer,
            sections=ordered,
            recent_events=events,
            relationships=edges,
            memory_ids=memory_ids,
            token_count=used_tokens,
            truncated=truncated,
        )

    def _is_duplicate(self, content: str, sections: dict[str, list[dict[str, Any]]]) -> bool:
        for items in sections.values():
            for item in items:
                if jaccard(content, item["content"]) >= DEDUPLICATION_THRESHOLD:
                    return True
        return False

    @staticmethod
    def summarize(text: str, max_tokens: int) -> str:
        return truncate_to_tokens(text, max_tokens)


def _as_type(value: Any) -> MemoryType:
    try:
        return MemoryType(str(value))
    except ValueError:
        return MemoryType.FACT
