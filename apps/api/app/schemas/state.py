"""Facts, conditions, lifecycle states and snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- conditions


class FactSpecOut(BaseModel):
    name: str
    type: str
    description: str
    values: list[str] = Field(default_factory=list)
    unit: str | None = None
    operators: list[str] = Field(default_factory=list)


class FactCatalogOut(BaseModel):
    facts: list[FactSpecOut]
    metadata_prefix: str = Field(
        description="Your own customer fields are addressable under this prefix without registering them."
    )
    examples: list[str] = Field(default_factory=list)


class ConditionIn(BaseModel):
    condition: str | dict[str, Any] = Field(
        description='Text such as `health.score < 60 and problems.entities contains "shopify"`, or the JSON tree.'
    )


class ConditionValidation(BaseModel):
    valid: bool
    text: str | None = None
    ast: dict[str, Any] | None = None
    facts: list[str] = Field(default_factory=list)
    error: str | None = None
    position: int | None = Field(default=None, description="Character offset of a parse error.")


class ConditionEvaluateIn(ConditionIn):
    customer_id: str = Field(min_length=1, max_length=255)


class LeafOut(BaseModel):
    fact: str
    op: str
    expected: Any = None
    actual: Any = None
    outcome: str
    note: str = ""
    evidence: list[str] = Field(default_factory=list)
    description: str


class EvaluationOut(BaseModel):
    """`outcome` is true, false or unknown; act on `matched`, which treats unknown as false."""

    outcome: str
    matched: bool
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    decisive: list[LeafOut] = Field(default_factory=list)
    leaves: list[LeafOut] = Field(default_factory=list)


class ConditionEvaluationOut(BaseModel):
    customer_id: str
    condition: str
    evaluation: EvaluationOut
    # Facts the reader's clearance removed; a leaf reading one of them reads "unknown".
    withheld_facts: list[str] = Field(default_factory=list)


class CustomerFactsOut(BaseModel):
    customer_id: str
    values: dict[str, Any]
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    withheld_facts: list[str] = Field(default_factory=list)
    computed_at: datetime


# ------------------------------------------------------------------ lifecycle


class CustomerStateOut(BaseModel):
    id: str
    track: str = Field(default="lifecycle", description="The lifecycle track this stay belongs to")
    state: str
    previous_state: str | None = None
    entered_at: datetime
    exited_at: datetime | None = None
    source: str = Field(description="initial, auto (a transition fired) or manual")
    transition: str | None = None
    reason: str | None = None
    evidence: list[str] = Field(default_factory=list)
    pinned: bool = False
    pinned_until: datetime | None = None
    actor_id: str | None = None
    evaluation: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(
        default_factory=list,
        description='The decisive clauses in words: "3 unresolved problems", "activity down 47%"',
    )


class TrackStateOut(BaseModel):
    track: str
    label: str
    primary: bool = False
    current: CustomerStateOut | None = None
    states: list[str] = Field(default_factory=list)


class CurrentStateOut(BaseModel):
    customer_id: str
    enabled: bool = True
    current: CustomerStateOut | None = None
    states: list[str] = Field(default_factory=list)
    # Every track, the primary first (§26 4.2).
    tracks: list[TrackStateOut] = Field(default_factory=list)


class StateSetIn(BaseModel):
    state: str = Field(min_length=1, max_length=64)
    track: str = Field(default="lifecycle", min_length=1, max_length=40)
    pin: bool = Field(default=True, description="Keep the machine from moving the customer until released.")
    pin_days: int | None = Field(default=None, ge=1, le=365)
    note: str | None = Field(default=None, max_length=500)


class StateRefreshOut(BaseModel):
    customer_id: str
    state: str | None = None
    moved: bool = False
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_id: str | None = None
    # Per track: {state, moved, transitions}.
    tracks: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TrackDefinitionOut(BaseModel):
    track: str
    label: str
    description: str | None = None
    enabled: bool = True
    states: list[str] = Field(default_factory=list)
    initial: str | None = None
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class LifecycleOut(BaseModel):
    enabled: bool = True
    states: list[str] = Field(default_factory=list)
    initial: str | None = None
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    # The extra tracks beside the primary machine (§26 4.2).
    tracks: list[TrackDefinitionOut] = Field(default_factory=list)


class TrackTemplateOut(BaseModel):
    name: str
    label: str
    description: str | None = None
    states: list[str]
    initial: str
    transitions: list[dict[str, Any]]


# ------------------------------------------------------------------ snapshots


class SnapshotSummaryOut(BaseModel):
    id: str
    taken_at: datetime
    reason: str
    event_id: str | None = None
    health_score: float | None = None
    health_band: str | None = None
    state: str | None = None
    plan: str | None = None
    trajectory: str | None = None
    open_problems: int = 0
    churn_risk: float | None = None
    expansion_score: float | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)


class SnapshotOut(SnapshotSummaryOut):
    facts: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    withheld_facts: list[str] = Field(default_factory=list)
