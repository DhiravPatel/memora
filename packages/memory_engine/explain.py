"""Why an event did, or did not, become a memory.

This is the answer to the question every integration asks on day one: *I sent an event and
nothing happened — why?* Before this, answering it meant reading the pipeline source, and
the pipeline makes six separate decisions before a memory exists. Any one of them can end
the story, and none of them used to leave a trace.

The same shapes serve two callers, deliberately:

* :meth:`MemoryEngine.preview_event` — a dry-run that writes nothing, so a developer can
  ask the question *before* sending anything;
* :meth:`MemoryEngine.process_event` — which records the outcome on the event it just
  processed, so the question is still answerable a week later.

Both are built from the pipeline's real decisions rather than from a second copy of the
rules, which is the only way a preview stays true as the rules change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# A stored outcome sits on every processed event, so it has to stay small. These caps are
# about the size of the table, not about what is interesting: an event that produced nine
# memories is already pathological, and the count is reported even when the detail is not.
MAX_STORED_MEMORIES = 10
MAX_STORED_ENTITIES = 20
# Enough to recognise the sentence, not the whole essay.
SNIPPET_LENGTH = 200


def _clip(value: str | None, limit: int = SNIPPET_LENGTH) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


# The reasons the pipeline stops, written once. The preview and the real run must give the
# same answer in the same words, or a developer who compares them learns to trust neither.

NO_TEXT = (
    "The payload contained no readable values. Every string, number and boolean is read "
    "as 'key: value' text, so this means the payload was empty, held only nested empties, "
    "or held only the reserved keys importance, metadata and _meta."
)


def below_threshold(importance: float, threshold: float) -> str:
    return (
        f"Importance {importance:.2f} is below this project's threshold of "
        f"{threshold:.2f}, so the event is stored but never read."
    )


def customer_missing() -> str:
    return "The customer was deleted before this event could be processed."


@dataclass(slots=True)
class MemoryPlan:
    """One candidate statement, and what consolidation decided to do with it."""

    content: str
    type: str
    action: str
    reason: str
    importance: float = 0.0
    confidence: float = 0.0
    similarity: float = 0.0
    rule: str | None = None
    # The memory this became. Only a real run has one — a preview creates nothing, so it
    # is null there, which is the honest difference between the two.
    memory_id: str | None = None
    # The existing memory this candidate was compared against. When the action is not
    # "create" it is the one being changed; when it *is* "create" it is the nearest miss —
    # which is the more useful of the two, because "it created a near-duplicate" is
    # answered by "the closest was 34% similar and your threshold is 45%".
    closest_memory_id: str | None = None
    closest_content: str | None = None
    # What the restriction policy made of it. Worth reporting even when nothing is
    # restricted: "this would be visible to every key" is also an answer.
    sensitivity: str = "normal"
    restricted_by: str | None = None
    # Which extraction rule produced the statement, so a surprising memory is traceable
    # to the thing that read it.
    extracted_by: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": _clip(self.content),
            "type": self.type,
            "action": self.action,
            "reason": self.reason,
            "importance": round(self.importance, 3),
            "confidence": round(self.confidence, 3),
            "similarity": round(self.similarity, 4),
            "rule": self.rule,
            "memory_id": self.memory_id,
            "closest_memory_id": self.closest_memory_id,
            "closest_content": _clip(self.closest_content),
            "sensitivity": self.sensitivity,
            "restricted_by": self.restricted_by,
            "extracted_by": self.extracted_by,
        }


@dataclass(slots=True)
class EntityPlan:
    """A thing the event mentioned, and whether the project already knew about it."""

    name: str
    type: str
    status: str  # "new" | "existing"
    entity_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "entity_id": self.entity_id,
        }


@dataclass(slots=True)
class EventExplanation:
    """The whole story for one event, in the order the pipeline decides it."""

    event_type: str
    customer_id: str
    # Did anything at all come of it?
    would_process: bool = False
    # Set when the pipeline stopped early. This is the field people are looking for.
    stop_reason: str | None = None
    # Stage 1: what the engine actually read, after redaction.
    text: str = ""
    text_length: int = 0
    redacted: bool = False
    # What redaction removed, by kind — "1 email, 2 phone numbers". Another quiet cause of
    # surprise: the memory does not say what the customer wrote, because the part that
    # mattered to them was an address the engine was never allowed to keep.
    redactions: list[dict[str, Any]] = field(default_factory=list)
    # Stage 2: triage.
    importance: float = 0.0
    threshold: float = 0.0
    # Stage 3 onwards.
    memories: list[MemoryPlan] = field(default_factory=list)
    entities: list[EntityPlan] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def creates(self) -> int:
        return sum(1 for plan in self.memories if plan.action == "create")

    @property
    def updates(self) -> int:
        return sum(1 for plan in self.memories if plan.action != "create")

    def summary(self) -> str:
        """One sentence, for a log line, a table cell or a CLI.

        Written to be readable by somebody who has not read the pipeline: it names the
        stage that ended the story rather than the function that returned early.
        """
        if self.stop_reason:
            return self.stop_reason
        if not self.memories:
            return "Nothing worth remembering was found in the text."
        # Memories only. A preview can tell a new entity from one the project already has,
        # because it is free to look; the real run cannot without an extra query per entity
        # on the ingestion path. Rather than let the two sentences differ — the one thing
        # this feature cannot afford — entities are reported in the list and not counted
        # here.
        parts = []
        if self.creates:
            parts.append(f"{self.creates} new {'memory' if self.creates == 1 else 'memories'}")
        if self.updates:
            parts.append(f"{self.updates} updated")
        return ", ".join(parts).capitalize() + "."

    def as_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        """The stored and transported form.

        ``include_text`` is off when this is written to the events table: the redacted text
        is already derivable from the event's own payload, and storing a second copy of
        every customer message is a retention problem wearing a debugging hat.
        """
        payload: dict[str, Any] = {
            "would_process": self.would_process,
            "stop_reason": self.stop_reason,
            "summary": self.summary(),
            "importance": round(self.importance, 3),
            "threshold": round(self.threshold, 3),
            "text_length": self.text_length,
            "redacted": self.redacted,
            "redactions": self.redactions,
            "memories": [plan.as_dict() for plan in self.memories[:MAX_STORED_MEMORIES]],
            "memory_count": len(self.memories),
            "entities": [entity.as_dict() for entity in self.entities[:MAX_STORED_ENTITIES]],
            "entity_count": len(self.entities),
        }
        if include_text:
            payload["text"] = self.text
        return payload
