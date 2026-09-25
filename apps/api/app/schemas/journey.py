"""A customer's journey as milestones (§26 6.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JourneyMemoryOut(BaseModel):
    id: str
    type: str
    content: str | None = None
    change: str = Field(description="What happened to it here: created, superseded, reported again, …")


class JourneyHealthSideOut(BaseModel):
    score: float | None = None
    band: str | None = None


class JourneyHealthOut(BaseModel):
    """Health at the snapshot the milestone's event led to, against the one before it."""

    before: JourneyHealthSideOut | None = None
    after: JourneyHealthSideOut | None = None
    delta: float | None = None
    drivers: list[str] = Field(default_factory=list, description="What else moved in that snapshot, in words.")
    snapshot_id: str | None = None


class JourneyTransitionOut(BaseModel):
    id: str
    track: str
    label: str
    before: str | None = None
    after: str
    reasons: list[str] = Field(default_factory=list)
    manual: bool = False


class JourneyEvidenceOut(BaseModel):
    memories: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    snapshots: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)


class MilestoneOut(BaseModel):
    id: str = Field(description="Stable across reads: the same moment is the same milestone.")
    at: datetime = Field(description="When it happened — the time of the event behind it.")
    recorded_at: datetime | None = Field(
        default=None, description="When Memora recorded it, when that is a day or more after it happened (imports)."
    )
    category: str = Field(
        description="account, usage, problem, plan, intent, preference, goal, health, lifecycle, activity, "
        "feedback or relationship."
    )
    kind: str
    title: str
    tone: str = Field(description="positive, negative or neutral.")
    importance: float
    topics: list[str] = Field(default_factory=list)
    what_happened: str
    why_it_matters: str | None = None
    memories: list[JourneyMemoryOut] = Field(default_factory=list, description="Which memories changed.")
    health: JourneyHealthOut | None = Field(default=None, description="What happened to health.")
    transitions: list[JourneyTransitionOut] = Field(
        default_factory=list, description="Which lifecycle transitions followed."
    )
    evidence: JourneyEvidenceOut
    detail: dict[str, Any] = Field(default_factory=dict)


class JourneyWindowOut(BaseModel):
    since: datetime | None = None
    until: datetime
    basis: str
    label: str
    note: str | None = None


class CustomerJourneyOut(BaseModel):
    customer_id: str
    customer: dict[str, Any]
    customer_since: datetime | None = None
    window: JourneyWindowOut
    summary: str
    milestones: list[MilestoneOut]
    counts: dict[str, int] = Field(default_factory=dict, description="Milestones by category, before `limit`.")
    total: int = Field(description="Milestones that qualified, before `limit`.")
    truncated: bool = False
    withheld: int = Field(default=0, description="Milestones about memories or goals this caller may not read.")
    generated_at: datetime
