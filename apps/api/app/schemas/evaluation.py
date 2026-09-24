"""Memory evaluation: retrieval and extraction cases, runs, regressions, the scorecard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvalSetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class ExpectationIn(BaseModel):
    """A memory an extraction case expects (or forbids). Any combination; at least one."""

    # A misspelt field would otherwise be dropped and the expectation silently weaker.
    model_config = ConfigDict(extra="forbid")

    type: str | None = Field(default=None, description="problem, preference, fact, …")
    contains: str | None = Field(
        default=None, max_length=200, description="Words it must say, matched in any form — like an expected phrase."
    )
    entity: str | None = Field(default=None, max_length=120, description="A product, integration or feature it names.")
    sensitivity: str | None = Field(default=None, description="normal or restricted")
    action: str | None = Field(default=None, description="What consolidation would do: create, merge, update, conflict, ignore")


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1, max_length=128)
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = Field(default=None, description="ISO time; default now.")


class EvalCaseIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255)
    kind: str = Field(default="retrieval", pattern="^(retrieval|extraction)$")
    question: str = Field(
        default="", max_length=2000, description="Retrieval: the question. Extraction: an optional label."
    )
    expected_memory_ids: list[str] = Field(
        default_factory=list, description="Exact, but invalidated when the customer is reprocessed."
    )
    expected_phrases: list[str] = Field(
        default_factory=list,
        description="Survive reprocessing: a memory matches when it mentions every word, in any form.",
    )
    notes: str | None = Field(default=None, max_length=1000)
    # Extraction cases (§26 4.3)
    event: EventIn | None = None
    expect: list[ExpectationIn] = Field(default_factory=list, description="Memories the event should become.")
    forbid: list[ExpectationIn] = Field(default_factory=list, description="Memories it must not become.")
    expect_nothing: bool = Field(default=False, description="The event should become no memory at all.")


class EvalCasesIn(BaseModel):
    cases: list[EvalCaseIn] = Field(min_length=1, max_length=200)


class EvalCaseOut(BaseModel):
    id: str
    customer_id: str
    kind: str = "retrieval"
    question: str
    expected_memory_ids: list[str] = Field(default_factory=list)
    expected_phrases: list[str] = Field(default_factory=list)
    event: dict[str, Any] | None = None
    expectations: dict[str, Any] | None = None
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
    proposed: bool = Field(default=False, description="Measured under proposed settings by a regression — never a baseline.")
    metrics: dict[str, Any] = Field(default_factory=dict)
    comparison: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class EvalRunOut(EvalRunSummaryOut):
    settings: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)
    baseline_run_id: str | None = None
    overrides: dict[str, Any] | None = Field(
        default=None, description="The proposed settings a regression run was measured under, never saved."
    )


class RegressionIn(BaseModel):
    settings: dict[str, Any] = Field(description="Settings to try, as you would save them — validated, never saved.")
    k: int = Field(default=10, ge=1, le=50)


class RegressionOut(BaseModel):
    safe: bool = Field(description="True when no case that passes today would fail.")
    summary: str
    newly_failing: list[dict[str, Any]] = Field(default_factory=list)
    newly_passing: list[dict[str, Any]] = Field(default_factory=list)
    current: EvalRunOut
    proposed: EvalRunOut


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
