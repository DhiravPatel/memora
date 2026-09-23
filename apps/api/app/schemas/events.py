"""Event ingestion schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from common.enums import EventStatus


class EventIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255, description="Your own customer id")
    event_type: str = Field(min_length=1, max_length=128)
    data: dict[str, Any] = Field(default_factory=dict)
    external_event_id: str | None = Field(default=None, max_length=255)
    occurred_at: datetime | None = None
    customer_email: str | None = Field(default=None, max_length=320)
    customer_name: str | None = Field(default=None, max_length=255)
    source: str = Field(default="api", max_length=64)

    @field_validator("event_type")
    @classmethod
    def _normalize_type(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")


class EventPreviewIn(BaseModel):
    """A dry run. Same shape as an event, minus the fields that only matter once stored."""

    customer_id: str = Field(min_length=1, max_length=255, description="Your own customer id")
    event_type: str = Field(min_length=1, max_length=128)
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    @field_validator("event_type")
    @classmethod
    def _normalize_type(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")


class MemoryPlanOut(BaseModel):
    """One statement the engine found, and what it would do with it."""

    content: str
    type: str
    action: str = Field(description="create, merge, update, supersede, conflict or ignore")
    reason: str
    importance: float = 0.0
    confidence: float = 0.0
    similarity: float = 0.0
    rule: str | None = None
    memory_id: str | None = Field(
        default=None,
        description="The memory this became. Null in a preview, which creates nothing.",
    )
    closest_memory_id: str | None = Field(
        default=None,
        description=(
            "The existing memory this was compared against: the one being changed when "
            "the action is not create, and the nearest miss when it is."
        ),
    )
    closest_content: str | None = None
    sensitivity: str = "normal"
    restricted_by: str | None = None
    extracted_by: str | None = None


class EntityPlanOut(BaseModel):
    name: str
    type: str
    status: str = Field(description="new, existing or touched")
    entity_id: str | None = None


class RedactionOut(BaseModel):
    kind: str
    count: int


class EventExplanationOut(BaseModel):
    """Why an event did, or would, become a memory — or why it would not."""

    would_process: bool = False
    # The field to read first. Null means the pipeline ran to the end.
    stop_reason: str | None = None
    summary: str = ""
    importance: float = 0.0
    threshold: float = 0.0
    text: str | None = Field(
        default=None,
        description="The text the engine read, after redaction. Preview only.",
    )
    text_length: int = 0
    redacted: bool = False
    redactions: list[RedactionOut] = Field(default_factory=list)
    memories: list[MemoryPlanOut] = Field(default_factory=list)
    memory_count: int = 0
    entities: list[EntityPlanOut] = Field(default_factory=list)
    entity_count: int = 0
    duration_ms: float = 0.0


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=500)


class EventAccepted(BaseModel):
    event_id: str
    status: Literal["accepted", "duplicate"] = "accepted"
    customer_id: str
    importance: float = 0.0
    queued: bool = True


class EventBatchAccepted(BaseModel):
    accepted: list[EventAccepted]
    duplicates: int = 0


class EventOut(BaseModel):
    id: str
    project_id: str
    customer_id: str
    event_type: str
    external_event_id: str | None = None
    data: dict[str, Any]
    source: str
    importance: float
    status: EventStatus
    occurred_at: datetime
    created_at: datetime
    processed_at: datetime | None = None
    error: str | None = None
    # Why this event did or did not become a memory. Null for events processed before
    # outcomes were recorded, which is different from "nothing happened".
    outcome: EventExplanationOut | None = None
