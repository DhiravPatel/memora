"""Agent session schemas: the contract an external AI agent codes against."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SessionStatus = Literal["open", "closed", "expired"]
Role = Literal["user", "agent", "system"]


class SessionOpen(BaseModel):
    customer_id: str = Field(min_length=1, description="Customer id or your own external id")
    agent: str = Field(default="agent", max_length=120, description="Which agent is running")
    # Pass your own conversation id and opening twice returns the same session rather than
    # starting a second one — safe to call on every reconnect.
    external_id: str | None = Field(default=None, max_length=255)
    channel: str | None = Field(default=None, max_length=64)
    token_budget: int | None = Field(default=None, ge=200, le=20000)
    metadata: dict[str, Any] | None = None


class PriorSessionOut(BaseModel):
    """What an earlier conversation with this customer established."""

    id: str
    agent: str
    summary: str
    turn_count: int = 0
    started_at: datetime
    closed_at: datetime | None = None


class SessionContextOut(BaseModel):
    """Everything the agent should know before it writes its first word."""

    text: str
    memory_ids: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    truncated: bool = False
    prior_sessions: list[PriorSessionOut] = Field(default_factory=list)


class TurnOut(BaseModel):
    id: str
    role: Role
    content: str
    occurred_at: datetime
    event_id: str | None = None
    retrieved_memory_ids: list[str] = Field(default_factory=list)


class SessionOut(BaseModel):
    id: str
    project_id: str
    customer_id: str
    external_id: str | None = None
    agent: str
    channel: str | None = None
    status: SessionStatus
    turn_count: int = 0
    started_at: datetime
    last_active_at: datetime
    closed_at: datetime | None = None
    summary: str | None = None
    summary_memory_id: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    resumed: bool = False
    context: SessionContextOut | None = None
    turns: list[TurnOut] = Field(default_factory=list)


class TurnCreate(BaseModel):
    role: Role = "user"
    content: str = Field(min_length=1, max_length=20000)
    # Whether this turn should become an event and be extracted into long-term memory.
    # Agent replies default to false: the product remembers what the customer said, not
    # what the assistant said back.
    remember: bool | None = None
    # Ask for fresh context for this turn (retrieval against what the customer just said).
    retrieve: bool = True
    occurred_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class TurnResponse(BaseModel):
    session_id: str
    turn: TurnOut
    context: SessionContextOut | None = None
    answer: str | None = None
    answer_confidence: float | None = None
    event_id: str | None = None
    turn_count: int = 0


class SessionClose(BaseModel):
    # Writing the summary is what makes the next session continuous; a caller can opt out
    # for a throwaway conversation.
    write_summary: bool = True
    outcome: str | None = Field(default=None, max_length=500)


class SessionCloseOut(BaseModel):
    session: SessionOut
    summary: str | None = None
    summary_memory_id: str | None = None
