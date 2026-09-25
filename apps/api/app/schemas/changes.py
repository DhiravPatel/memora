"""What changed about a customer, and what they looked like then and now (§26 4.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChangeOut(BaseModel):
    type: str = Field(
        description="subscription, lifecycle, health, risk, trajectory, problem, intent, preference, goal, "
        "feedback, relationship, fact, memory, signal or activity."
    )
    kind: str = Field(description="What happened to it: opened, resolved, recurring, changed, moved, crossed, …")
    title: str
    before: str | None = None
    after: str | None = None
    detected_at: datetime
    evidence: list[str] = Field(default_factory=list, description="The memory, goal and state ids behind it.")
    source: str = Field(description="The record it was read from: memory, version, goal, lifecycle, snapshot, signal, activity.")
    track: str | None = None
    reasons: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    importance: float
    topics: list[str] = Field(default_factory=list, description="What it is about: the products, integrations and features its memory names.")


class WindowOut(BaseModel):
    since: datetime
    until: datetime
    basis: str = Field(
        description="How `since` was chosen: span, time, snapshot, last_session, last_run, last_view or default."
    )
    value: str | None = None
    found: bool = Field(default=True, description="False when `last_session`/`last_run` had nothing to go back to.")
    note: str | None = None
    label: str


class CustomerAtOut(BaseModel):
    at: datetime
    live: bool = Field(description="Computed now rather than read from a snapshot.")
    snapshot_id: str | None = None
    taken_at: datetime | None = None
    state: dict[str, Any] | None = Field(
        default=None, description="Plan, health, lifecycle and tracks, problems, goals, channel, signals. Null when nothing was recorded yet."
    )
    description: str | None = None


class ChangesOut(BaseModel):
    customer_id: str
    window: WindowOut
    summary: str
    changes: list[ChangeOut]
    counts: dict[str, int] = Field(default_factory=dict)
    total: int
    truncated: bool = False
    withheld: int = Field(default=0, description="Changes about records this caller may not see.")
    then: CustomerAtOut
    now: CustomerAtOut


class FactDifferenceOut(BaseModel):
    fact: str
    before: Any = None
    after: Any = None
    added: list[str] | None = None
    removed: list[str] | None = None


class CompareOut(BaseModel):
    customer_id: str
    then: CustomerAtOut
    now: CustomerAtOut
    differences: list[FactDifferenceOut]
    summary: str
