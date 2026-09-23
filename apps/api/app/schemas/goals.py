"""Goal tracking schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GoalStatusName = Literal["open", "progressing", "achieved", "stalled", "abandoned"]


class GoalEvidenceOut(BaseModel):
    kind: str
    at: datetime | None = None
    memory_id: str | None = None
    event_id: str | None = None
    match: float | None = None
    cue: str | None = None
    note: str | None = None


class GoalOut(BaseModel):
    id: str
    customer_id: str
    statement: str
    status: GoalStatusName
    progress: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    keywords: list[str] = Field(default_factory=list)
    memory_id: str | None = None
    evidence: list[GoalEvidenceOut] = Field(default_factory=list)
    opened_at: datetime
    last_signal_at: datetime
    closed_at: datetime | None = None
    closed_reason: str | None = None
    # Set when a person overrode the tracked status; the tracker then leaves it alone.
    overridden: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalUpdate(BaseModel):
    """A human verdict on a goal. Recorded as an override, and audited."""

    status: GoalStatusName
    note: str | None = Field(default=None, max_length=500)


class GoalSummaryOut(BaseModel):
    """Counts for a customer header or a project overview."""

    total: int = 0
    open: int = 0
    progressing: int = 0
    achieved: int = 0
    stalled: int = 0
    abandoned: int = 0
    summary: str = ""
