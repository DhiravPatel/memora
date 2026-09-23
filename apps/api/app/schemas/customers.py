"""Customer schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class CustomerUpsert(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = None
    name: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None


class CustomerOut(BaseModel):
    id: str
    project_id: str
    external_id: str
    email: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_event_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TimelineEntry(BaseModel):
    kind: str  # "event" | "memory"
    id: str
    title: str
    detail: str | None = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class CustomerTimeline(BaseModel):
    customer: CustomerOut
    entries: list[TimelineEntry]


class Customer360(BaseModel):
    """Everything worth knowing about one customer, in one response.

    ``sections`` is a map rather than a fixed set of fields on purpose: a caller that asks
    for three sections gets three keys, not eleven with eight nulls, so "absent" and "empty"
    stay distinguishable — an agent must be able to tell "no open problems" from "I did not
    look at problems".
    """

    customer: dict[str, Any]
    summary: str = Field(description="One sentence, composed from the sections that were built")
    sections: dict[str, Any] = Field(default_factory=dict)
    withheld: int = Field(
        default=0, description="Memories this caller's clearance hid, across every section"
    )
    generated_at: datetime
