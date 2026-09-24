"""Structured contracts inside the memory engine.

Everything the language engine produces is validated against these models before it can
touch the database, so a malformed rule output or an out-of-range score can never become
a memory. The models also define the shape the dashboard and API expose, which is why
they carry the provenance fields (``rule``, ``attributes``) as first-class data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from common.enums import ConsolidationAction, EntityType, MemoryType, RelationshipType


class ExtractedMemory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: MemoryType = MemoryType.FACT
    content: str = Field(min_length=3, max_length=2000)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Provenance: which rule produced this statement and what it observed.
    source: str = Field(default="text", max_length=32)
    rule: str = Field(default="", max_length=64)
    entity_names: list[str] = Field(default_factory=list, max_length=20)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def _clean(cls, value: str) -> str:
        return " ".join(value.split())


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: EntityType = EntityType.OTHER
    name: str = Field(min_length=1, max_length=200)
    external_id: str | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    rule: str = Field(default="", max_length=64)

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return " ".join(value.split())


class ExtractedRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = Field(min_length=1, max_length=200)
    type: RelationshipType = RelationshipType.RELATED_TO
    target: str = Field(min_length=1, max_length=200)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=10)
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=30)
    relationships: list[ExtractedRelationship] = Field(default_factory=list, max_length=30)
    stats: dict[str, Any] = Field(default_factory=dict)


class ConsolidationDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: ConsolidationAction
    content: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=500)
    similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    memory_id: str = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class QueryAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str = Field(default="", max_length=4000)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


@dataclass(slots=True)
class NormalizedEvent:
    """An event after validation, redaction and text flattening."""

    event_id: str
    project_id: str
    customer_id: str
    event_type: str
    data: dict[str, Any]
    text: str
    occurred_at: datetime
    importance: float


@dataclass(slots=True)
class ScoredMemory:
    """A retrieval candidate plus the reason it was retrieved and how it ranked."""

    memory: Any  # database.models.Memory
    similarity: float = 0.0
    keyword_score: float = 0.0
    recency: float = 0.0
    relationship_relevance: float = 0.0
    # How much the memory is *about* what was asked (nlp.concepts), and which concepts
    # matched — the explanation for a paraphrase match that shares no words.
    concept_score: float = 0.0
    matched_concepts: list[str] = field(default_factory=list)
    score: float = 0.0
    strategies: set[str] = field(default_factory=set)

    @property
    def id(self) -> str:
        return self.memory.id

    def explain(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory.id,
            "score": round(self.score, 4),
            "similarity": round(self.similarity, 4),
            "keyword_score": round(self.keyword_score, 4),
            "concept_score": round(self.concept_score, 4),
            "matched_concepts": list(self.matched_concepts),
            "recency": round(self.recency, 4),
            "relationship_relevance": round(self.relationship_relevance, 4),
            "importance": round(float(self.memory.importance), 4),
            "confidence": round(float(self.memory.confidence), 4),
            "strategies": sorted(self.strategies),
        }


@dataclass(slots=True)
class ProcessingResult:
    """What happened to one event as it passed through the pipeline."""

    event_id: str
    processed: bool
    skipped_reason: str | None = None
    created_memory_ids: list[str] = field(default_factory=list)
    updated_memory_ids: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    relationship_ids: list[str] = field(default_factory=list)
    # Deterministic engine: cost is CPU, so the useful counters are work done, not tokens.
    duration_ms: float = 0.0
    candidates_considered: int = 0
