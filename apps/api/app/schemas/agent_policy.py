"""Agent profiles, guardrail checks, approvals and agent runs (§26 3.1–3.4)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.state import SnapshotOut
from common.enums import MemoryType

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
MAX_REQUEST_BYTES = 4096
MAX_ACTIONS = 60


def _action_list(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        action = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if not action:
            continue
        if len(action) > 80:
            raise ValueError(f"Action names are at most 80 characters: {action[:20]}…")
        cleaned.append(action)
    if len(cleaned) > MAX_ACTIONS:
        raise ValueError(f"At most {MAX_ACTIONS} actions.")
    return list(dict.fromkeys(cleaned))


# -------------------------------------------------------------------- profiles


class AgentProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="e.g. support-agent. Lowercase, no spaces.")
    description: str | None = Field(default=None, max_length=500)
    readable_types: list[MemoryType] = Field(
        default_factory=list, description="Memory types this agent may read. Empty means all of them."
    )
    can_read_restricted: bool = Field(
        default=False,
        description="Restricted memories also need the key's memory:restricted scope.",
    )
    allowed_actions: list[str] = Field(
        default_factory=list, description="The only actions this agent may take. Empty means any not denied."
    )
    denied_actions: list[str] = Field(default_factory=list, description="Actions this agent may never take.")

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        name = value.strip().lower().replace(" ", "-")
        if not _NAME.match(name):
            raise ValueError("Use lowercase letters, digits, '-', '_' or '.', starting with a letter or digit.")
        return name

    @field_validator("allowed_actions", "denied_actions")
    @classmethod
    def _actions(cls, value: list[str]) -> list[str]:
        return _action_list(value)


class AgentProfileUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    readable_types: list[MemoryType] | None = None
    can_read_restricted: bool | None = None
    allowed_actions: list[str] | None = None
    denied_actions: list[str] | None = None

    @field_validator("allowed_actions", "denied_actions")
    @classmethod
    def _actions(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _action_list(value)


class AgentProfileOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    readable_types: list[str] = Field(default_factory=list)
    can_read_restricted: bool = False
    allowed_actions: list[str] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    keys: int = Field(default=0, description="Live API keys acting as this profile.")
    created_at: datetime
    updated_at: datetime


class KeyProfileIn(BaseModel):
    agent_profile_id: str | None = Field(
        default=None, description="The profile this key acts as, or null to unbind it."
    )


# ---------------------------------------------------------------------- checks


class AgentCheckIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=255)
    action: str = Field(
        min_length=1, max_length=80, description="e.g. offer_upgrade, contact_customer, offer_discount, close_ticket"
    )
    request: dict[str, Any] = Field(
        default_factory=dict,
        description="Details of the proposed action: channel, amount, topic, plan, and anything else your rules read.",
    )
    approval_id: str | None = Field(
        default=None, max_length=64, description="An approved request to redeem for exactly this action."
    )
    session_id: str | None = Field(default=None, max_length=64)
    agent: str | None = Field(
        default=None, max_length=120, description="A label for an unbound key. A bound key is labelled by its profile."
    )
    dry_run: bool = Field(
        default=False, description="Decide without recording the check or filing an approval request."
    )

    @field_validator("request")
    @classmethod
    def _request(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError) as exc:  # pragma: no cover - pydantic already parsed JSON
            raise ValueError("The request must be plain JSON.") from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError(f"The request is at most {MAX_REQUEST_BYTES} bytes of JSON.")
        return value


class ReasonOut(BaseModel):
    rule: str
    source: str = Field(description="profile, builtin or project")
    decision: str
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    evaluation: dict[str, Any] | None = None


class EvidenceMemoryOut(BaseModel):
    """A memory a reason rests on, in its own words — what a reviewer reads before deciding."""

    id: str
    type: str | None = None
    content: str | None = Field(default=None, description="[withheld] for a reviewer who may not read it.")
    visible: bool = True
    status: str | None = None
    first_seen_at: datetime | None = None


class CustomerSummaryOut(BaseModel):
    """The customer as last recorded, for context beside a request."""

    name: str | None = None
    health_score: float | None = None
    health_band: str | None = None
    state: str | None = None
    plan: str | None = None
    open_problems: int | None = None
    taken_at: datetime | None = None


class ApprovalOut(BaseModel):
    id: str
    customer_id: str
    check_id: str
    agent: str | None = None
    action: str
    request: dict[str, Any] = Field(default_factory=dict)
    reasons: list[ReasonOut] = Field(default_factory=list)
    status: str = Field(description="pending, approved, rejected, expired or used")
    note: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    used_at: datetime | None = None
    expires_at: datetime
    created_at: datetime
    evidence_memories: list[EvidenceMemoryOut] = Field(
        default_factory=list, description="The memories the reasons cite, in their own words, as the reviewer may see them."
    )
    withheld_evidence: int = Field(default=0, description="Cited memories the reviewer may not read.")
    customer: CustomerSummaryOut | None = None
    action_id: str | None = Field(default=None, description="The gateway action waiting on this approval, if any.")


class AgentCheckOut(BaseModel):
    id: str
    customer_id: str
    action: str
    decision: str = Field(description="allow, require_approval or deny")
    allowed: bool
    summary: str
    reasons: list[ReasonOut] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    approval: ApprovalOut | None = None
    agent: str | None = None
    profile: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    snapshot_id: str | None = None
    session_id: str | None = None
    checked_at: datetime


class DashboardCheckIn(AgentCheckIn):
    """A policy simulation from the dashboard: never recorded, optionally as a profile."""

    profile_id: str | None = Field(default=None, max_length=64)


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class GuardrailCatalogOut(BaseModel):
    actions: list[dict[str, str]]
    builtin_rules: list[dict[str, str]]
    decisions: list[str]


class AgentActivityOut(BaseModel):
    window_days: int
    checks: dict[str, int] = Field(default_factory=dict)
    approvals: dict[str, int] = Field(default_factory=dict)
    top_rules: list[dict[str, Any]] = Field(default_factory=list)
    runs: int = 0


# ------------------------------------------------------------------------ runs


class RunSummaryOut(BaseModel):
    id: str
    kind: str = Field(description="query (an answer) or context (a briefing)")
    customer_id: str | None = None
    query: str
    answer: str | None = None
    agent: str | None = None
    session_id: str | None = None
    api_key_id: str | None = None
    snapshot_id: str | None = None
    memory_count: int = 0
    cited_count: int = 0
    withheld: int = 0
    latency_ms: int = 0
    created_at: datetime


class RunOut(RunSummaryOut):
    memory_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)


class RunMemoryOut(BaseModel):
    id: str
    rank: int
    type: str | None = None
    score: float = 0.0
    strategies: list[str] = Field(default_factory=list)
    cited: bool = False
    scores: dict[str, Any] = Field(default_factory=dict)
    visible: bool = True
    content_then: str | None = Field(default=None, description="The words as they were when the agent saw them.")
    content_now: str | None = None
    status_now: str | None = Field(default=None, description="active, superseded, expired… or deleted")
    changed_since: list[dict[str, Any]] = Field(default_factory=list)


class RunExplanationOut(BaseModel):
    run: RunSummaryOut
    narrative: list[str] = Field(default_factory=list)
    memories: list[RunMemoryOut] = Field(default_factory=list)
    held_back: dict[str, Any] = Field(default_factory=dict)
    state_then: SnapshotOut | None = None
    checks: list[AgentCheckOut] = Field(default_factory=list)


# ------------------------------------------------------------ decision trace (§26 4.4)


class TraceGivenOut(RunMemoryOut):
    verdict: str = Field(description="cited (the answer rests on it), given (in a context), or not_cited")
    why: str


class TraceIgnoredOut(BaseModel):
    id: str
    type: str | None = None
    reason: str = Field(
        description="below_cut, type_cap, token_budget, superseded, expired, withheld_restricted or withheld_profile"
    )
    why: str
    visible: bool = True
    content: str | None = Field(default=None, description="The memory's words, or [withheld] for a reader who may not see it.")
    score: float | None = None
    position: int | None = Field(default=None, description="Its place in the full ranking, for ranking reasons.")
    match: float | None = Field(default=None, description="How closely it matched the question, for unseen memories.")
    superseded_by: str | None = None
    replacement_rank: int | None = Field(default=None, description="Where the memory that replaced it ranked in what the agent was given.")


class TraceDecisionOut(BaseModel):
    kind: str = Field(description="answer or context")
    answer: str | None = None
    strategy: str | None = None
    confidence: float | None = None
    reasoning: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    token_count: int | None = None
    token_budget: int | None = None
    truncated: bool | None = None


class RunTraceOut(BaseModel):
    run: RunSummaryOut
    question: str
    narrative: list[str] = Field(default_factory=list)
    given: list[TraceGivenOut] = Field(default_factory=list)
    ignored: list[TraceIgnoredOut] = Field(default_factory=list)
    decision: TraceDecisionOut
    cut: dict[str, Any] = Field(default_factory=dict)
    held_back: dict[str, Any] = Field(default_factory=dict)
    state_then: SnapshotOut | None = None
    checks: list[AgentCheckOut] = Field(default_factory=list)
    recorded: bool = Field(
        default=True, description="False for runs recorded before traces kept what was considered: `ignored` is then incomplete."
    )


# ----------------------------------------------------------- action gateway (§26 4.5)


class ActionRequestIn(AgentCheckIn):
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Your own id for this action. Sending it again returns the same action rather than a second one.",
    )


class ActionCompleteIn(BaseModel):
    outcome: str = Field(pattern="^(done|failed|cancelled)$", description="done, failed or cancelled")
    note: str | None = Field(default=None, max_length=2000)
    external_ref: str | None = Field(
        default=None, max_length=255, description="Your system's id for what was done — a refund id, a ticket number."
    )


class ActionOut(BaseModel):
    id: str
    customer_id: str
    action: str
    request: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(
        description="allowed, pending_approval, denied, done, failed, cancelled or expired"
    )
    decision: str
    summary: str
    next_step: str = Field(description="What the agent should do now, in one sentence.")
    reasons: list[ReasonOut] = Field(default_factory=list)
    approval: ApprovalOut | None = None
    check_id: str | None = None
    agent: str | None = None
    session_id: str | None = None
    idempotency_key: str | None = None
    outcome_note: str | None = None
    external_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
