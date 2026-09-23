"""Predictive signal and recommendation schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Trajectory = Literal["improving", "steady", "declining"]
Priority = Literal["now", "soon", "when_you_can"]


class SignalOut(BaseModel):
    key: str
    label: str
    direction: Literal["risk", "opportunity"]
    strength: float = Field(ge=0, le=1)
    horizon_days: int
    rationale: str
    memory_ids: list[str] = Field(default_factory=list)
    observed: float = 0.0


class SignalPointOut(BaseModel):
    """One day of the stored series."""

    captured_on: date
    health_score: float
    churn_risk: float = Field(ge=0, le=1)
    expansion_score: float = Field(ge=0, le=1)
    trajectory: Trajectory


class SignalReportOut(BaseModel):
    customer_id: str
    external_id: str
    name: str | None = None
    trajectory: Trajectory
    churn_risk: float = Field(ge=0, le=1)
    expansion_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    headline: str
    health_score: float = Field(ge=0, le=100)
    signals: list[SignalOut] = Field(default_factory=list)
    measurements: dict[str, float] = Field(default_factory=dict)
    series: list[SignalPointOut] = Field(default_factory=list)
    computed_at: datetime


class PortfolioSignalsOut(BaseModel):
    project_id: str
    customers: list[SignalReportOut] = Field(default_factory=list)
    trajectories: dict[str, int] = Field(default_factory=dict)


class RecommendationOut(BaseModel):
    key: str
    action: str
    rationale: str
    category: str
    urgency: float = Field(ge=0, le=1)
    priority: Priority
    memory_ids: list[str] = Field(default_factory=list)
    goal_ids: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    playbook: list[str] = Field(default_factory=list)


class RecommendationsOut(BaseModel):
    customer_id: str
    external_id: str
    summary: str
    trajectory: Trajectory
    churn_risk: float = Field(ge=0, le=1)
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    computed_at: datetime
