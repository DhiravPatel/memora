"""Freshness and drift (§26 5.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FreshnessState = Literal["active", "aging", "stale", "outdated", "conflicted", "expired", "superseded"]
DriftKind = Literal["channel", "plan", "usage", "quiet_problem"]
DriftStatus = Literal["open", "confirmed", "kept", "dismissed", "cleared"]


class DriftRefOut(BaseModel):
    id: str
    kind: str
    summary: str


class FreshnessOut(BaseModel):
    state: FreshnessState
    effective_confidence: float = Field(
        ge=0, le=1, description="Stored confidence, halved every window without evidence, never below 15% of it."
    )
    evidence_at: datetime = Field(description="The later of the last time the customer said it and the last time a person confirmed it.")
    days_since_evidence: int
    window_days: int = Field(description="Days without evidence after which this type of memory is stale.")
    reasons: list[str] = Field(default_factory=list)
    contradicted_at: datetime | None = None
    drift: list[DriftRefOut] = Field(default_factory=list, description="Open drift flags on the memory.")


class DriftMemoryOut(BaseModel):
    id: str
    type: str | None = None
    content: str | None = None
    status: str | None = None
    last_seen_at: datetime | None = None


class DriftCustomerOut(BaseModel):
    id: str
    external_id: str | None = None
    name: str | None = None


class DriftOut(BaseModel):
    id: str
    kind: DriftKind
    kind_label: str
    status: DriftStatus
    stated: str = Field(description="What the memory says: a channel, a plan, a feature, a problem.")
    observed: str | None = Field(default=None, description="What the evidence says instead, where there is one thing.")
    summary: str
    counts: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list, description="Event and session ids behind the flag, newest first.")
    since: datetime = Field(description="Where counting started: the memory's last evidence, or a dismissal.")
    detected_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    resolved_by_type: str | None = None
    note: str | None = None
    replacement_memory_id: str | None = Field(default=None, description="The memory confirming wrote.")
    memory: DriftMemoryOut
    customer: DriftCustomerOut


class DriftDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class DriftRunOut(BaseModel):
    opened: list[str] = Field(default_factory=list)
    refreshed: list[str] = Field(default_factory=list)
    cleared: list[str] = Field(default_factory=list)
    open: list[DriftOut] = Field(default_factory=list, description="The customer's open flags after the run.")


class MemoryFreshnessOut(BaseModel):
    id: str
    type: str
    content: str
    importance: float
    confidence: float
    last_seen_at: datetime
    freshness: FreshnessOut


class CustomerFreshnessOut(BaseModel):
    customer_id: str
    counts: dict[str, int] = Field(description="Standing memories by state.")
    total: int
    needs_attention: int = Field(description="Stale, outdated and conflicted.")
    stale_share: float | None = None
    memories: list[MemoryFreshnessOut] = Field(default_factory=list, description="Memories not fresh, most concerning first.")
    drift: list[DriftOut] = Field(default_factory=list)
    windows: dict[str, int]
    fallback_window: int
    withheld: int = 0
    truncated: bool = False
    computed_at: datetime
