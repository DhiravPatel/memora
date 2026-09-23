"""Memory, entity and relationship schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from common.enums import EntityType, MemorySource, MemoryStatus, MemoryType, Sensitivity


class MemoryOut(BaseModel):
    id: str
    project_id: str
    customer_id: str
    type: MemoryType
    content: str
    importance: float
    confidence: float
    status: MemoryStatus
    source: MemorySource
    # "restricted" means the project's policy gated this memory; only a caller with the
    # memory:restricted scope (or an admin in the dashboard) ever receives one.
    sensitivity: Sensitivity = Sensitivity.NORMAL
    source_event_ids: list[str] = Field(default_factory=list)
    evidence_count: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryCreate(BaseModel):
    customer_id: str
    type: MemoryType = MemoryType.FACT
    content: str = Field(min_length=3, max_length=2000)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class MemoryVersionOut(BaseModel):
    id: str
    memory_id: str
    previous_content: str | None = None
    new_content: str
    reason: str
    source_event_id: str | None = None
    created_at: datetime


class MemoryLinkOut(BaseModel):
    """An inferred relationship between two memories, with the reason it was drawn."""

    id: str
    link_type: str
    confidence: float
    rationale: str | None = None
    direction: str = "outgoing"  # outgoing: this memory → the other one
    other_memory_id: str
    other_content: str | None = None
    other_type: str | None = None


class MemoryDetail(MemoryOut):
    versions: list[MemoryVersionOut] = Field(default_factory=list)
    entities: list[EntityOut] = Field(default_factory=list)
    links: list[MemoryLinkOut] = Field(default_factory=list)


class EntityOut(BaseModel):
    id: str
    project_id: str
    type: EntityType
    name: str
    external_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    mention_count: int = 1
    created_at: datetime


class RelationshipOut(BaseModel):
    id: str
    source_entity_id: str
    relationship_type: str
    target_entity_id: str
    confidence: float


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    mention_count: int = 1
    is_root: bool = False


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    confidence: float


class MemoryGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class CausalChainStep(BaseModel):
    memory_id: str
    content: str
    type: str
    link_type: str
    confidence: float
    rationale: str | None = None
    occurred_at: datetime


class CausalChain(BaseModel):
    """Why an outcome happened, as far as the memory can support."""

    outcome_memory_id: str
    outcome_content: str
    occurred_at: datetime
    steps: list[CausalChainStep] = Field(default_factory=list)


class CustomerLinks(BaseModel):
    customer_id: str
    links: list[MemoryLinkOut] = Field(default_factory=list)
    chains: list[CausalChain] = Field(default_factory=list)


MemoryDetail.model_rebuild()


class LearnedTermOut(BaseModel):
    """One vocabulary pair: mined from the corpus, or taught by a person."""

    term: str
    synonym: str
    # Normalised PMI, 0-1: 1 means the two only ever appear together. Curated pairs are 1.
    score: float = Field(ge=0, le=1)
    # How many memories back the pair. Zero for a curated entry — a person is the evidence.
    support: int = 0
    source: Literal["mined", "curated"] = "mined"
    status: Literal["active", "rejected"] = "active"
    note: str | None = None
    mined_at: datetime


class GlossaryEntryIn(BaseModel):
    """Teach the project that two words mean the same thing here."""

    term: str = Field(min_length=2, max_length=64)
    synonym: str = Field(min_length=2, max_length=64)
    note: str | None = Field(default=None, max_length=255)


class VocabularyOut(BaseModel):
    project_id: str
    terms: list[LearnedTermOut] = Field(default_factory=list)
    total: int = 0
    curated: int = 0
    rejected: int = 0
    last_mined_at: datetime | None = None
