"""Customer health and memory feedback schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthFactorOut(BaseModel):
    key: str
    label: str
    contribution: float
    count: int = 0
    memory_ids: list[str] = Field(default_factory=list)


class HealthOut(BaseModel):
    customer_id: str
    external_id: str
    name: str | None = None
    score: float = Field(ge=0, le=100)
    band: Literal["healthy", "watch", "at_risk", "critical"]
    churn_risk: float = Field(ge=0, le=1)
    explanation: str
    factors: list[HealthFactorOut] = Field(default_factory=list)
    memories_considered: int = 0
    events_considered: int = 0
    computed_at: datetime


class PortfolioHealthOut(BaseModel):
    project_id: str
    customers: list[HealthOut]
    bands: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    verdict: Literal["confirm", "reject", "correct"]
    content: str | None = Field(
        default=None, max_length=2000, description="Required when verdict is 'correct'."
    )
    note: str | None = Field(default=None, max_length=500)


class FeedbackOut(BaseModel):
    memory_id: str
    verdict: str
    status: str
    confidence: float
    replacement_memory_id: str | None = None
