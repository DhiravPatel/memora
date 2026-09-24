"""The memory quality report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class QualityComponent(BaseModel):
    key: str
    label: str
    score: int | None = Field(default=None, description="0–100; null when there is not yet anything to score")
    detail: str


class QualityDiagnostic(BaseModel):
    key: str
    severity: str = Field(description="info, warning or critical")
    title: str
    detail: str
    fix: dict[str, Any] = Field(default_factory=dict)
    examples: list[Any] = Field(default_factory=list)


class QualityReport(BaseModel):
    window_days: int
    score: int | None = Field(default=None, description="The mean of the components that could be scored")
    components: list[QualityComponent]
    metrics: dict[str, Any]
    diagnostics: list[QualityDiagnostic]
    computed_at: datetime
