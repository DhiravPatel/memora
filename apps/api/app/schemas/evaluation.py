"""Retrieval evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EvalSetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class EvalCaseIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255)
    question: str = Field(min_length=1, max_length=2000)
    expected_memory_ids: list[str] = Field(
        default_factory=list, description="Exact, but invalidated when the customer is reprocessed."
    )
    expected_phrases: list[str] = Field(
        default_factory=list,
        description="Survive reprocessing: a memory matches when it mentions every word, in any form.",
    )
    notes: str | None = Field(default=None, max_length=1000)


class EvalCasesIn(BaseModel):
    cases: list[EvalCaseIn] = Field(min_length=1, max_length=200)


class EvalCaseOut(BaseModel):
    id: str
    customer_id: str
    question: str
    expected_memory_ids: list[str] = Field(default_factory=list)
    expected_phrases: list[str] = Field(default_factory=list)
    notes: str | None = None
    source: str = "manual"
    created_at: datetime


class EvalRunIn(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    k: int = Field(default=10, ge=1, le=50, description="How many memories retrieval returns per question.")
    wait: bool = Field(
        default=True,
        description="Run inline and return the result — for sets of up to 100 cases. Larger sets always run in the worker.",
    )


class EvalRunSummaryOut(BaseModel):
    id: str
    set_id: str
    status: str
    label: str | None = None
    k: int = 10
    metrics: dict[str, Any] = Field(default_factory=dict)
    comparison: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class EvalRunOut(EvalRunSummaryOut):
    settings: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)
    baseline_run_id: str | None = None


class EvalSetOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    cases: int = 0
    latest_run: EvalRunSummaryOut | None = None
    created_at: datetime


class EvalSetDetail(EvalSetOut):
    case_list: list[EvalCaseOut] = Field(default_factory=list)
    runs: list[EvalRunSummaryOut] = Field(default_factory=list)


class EvalSuggestion(BaseModel):
    customer_id: str
    question: str
    asked_at: datetime
    answer: str | None = None
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
