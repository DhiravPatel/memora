"""Memory query and AI context schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from common.enums import MemoryType


class MemoryQueryRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)
    include_trace: bool = Field(
        default=False, description="Return why each memory was retrieved and ranked."
    )
    session_id: str | None = Field(
        default=None,
        max_length=64,
        description="The agent session this question belongs to, so its run is filed with it.",
    )


class MemorySearchRequest(BaseModel):
    customer_id: str | None = None
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)
    types: list[MemoryType] | None = None


class EvidenceOut(BaseModel):
    event_id: str


class QueriedMemory(BaseModel):
    id: str
    type: str
    content: str
    importance: float
    confidence: float
    score: float | None = None
    retrieved_by: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)


class MemoryQueryResponse(BaseModel):
    answer: str
    confidence: float
    memories: list[QueriedMemory]
    sources: list[EvidenceOut]
    trace: dict[str, Any] | None = None
    run_id: str | None = Field(
        default=None, description="The recorded agent run — GET /v1/agent/runs/{run_id}/explain."
    )


class MemorySearchResponse(BaseModel):
    memories: list[QueriedMemory]
    trace: dict[str, Any] | None = None


class ContextRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255)
    task: str | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)
    token_budget: int | None = Field(default=None, ge=200, le=20000)
    format: str = Field(default="json", pattern="^(json|text)$")
    session_id: str | None = Field(default=None, max_length=64)


class ContextResponse(BaseModel):
    customer_context: dict[str, Any]
    prompt_text: str | None = None
    token_count: int = 0
    truncated: bool = False
    run_id: str | None = None
