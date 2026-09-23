"""Usage, overview and audit schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class OverviewOut(BaseModel):
    project_id: str
    total_customers: int
    total_events: int
    total_memories: int
    events_processed: int
    events_pending: int
    events_failed: int
    ai_queries: int
    total_entities: int
    total_relationships: int
    memories_by_type: dict[str, int] = Field(default_factory=dict)


class UsagePoint(BaseModel):
    day: date
    metric: str
    count: int


class UsageOut(BaseModel):
    project_id: str
    totals: dict[str, int]
    series: list[UsagePoint]


class AuditLogOut(BaseModel):
    id: str
    action: str
    actor_type: str
    actor_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QueryLogOut(BaseModel):
    id: str
    kind: str
    query: str
    answer: str | None = None
    customer_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    created_at: datetime
