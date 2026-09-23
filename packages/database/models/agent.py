"""Agent sessions: the memory an AI agent keeps *between* conversations.

An agent that only sees the current conversation starts from nothing every time. A session
gives it two things: the customer's accumulated memory on the way in, and a durable record
of what this conversation established on the way out. Closing a session writes a summary
memory, which is what the *next* session reads — that loop is the cross-session memory.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import AgentSessionStatus, TurnRole
from database.base import ID_LENGTH, Base, TimestampMixin


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        # Resuming is idempotent: the same external id always returns the same session.
        UniqueConstraint(
            "project_id", "external_id", name="uq_agent_sessions_project_id_external_id"
        ),
        Index("ix_agent_sessions_customer_status", "customer_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    # The caller's own conversation id, so a dropped connection can resume rather than
    # starting a second session for the same conversation.
    external_id: Mapped[str | None] = mapped_column(String(255))
    agent: Mapped[str] = mapped_column(String(120), nullable=False, default="agent")
    channel: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[AgentSessionStatus] = mapped_column(
        String(32), nullable=False, default=AgentSessionStatus.OPEN, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    turn_count: Mapped[int] = mapped_column(nullable=False, default=0)
    # What this session established, written on close and read by the next session.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_memory_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    # Memories this session created, and the ones it read on the way in.
    memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class AgentTurn(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (Index("ix_agent_turns_session_occurred", "session_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[TurnRole] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set when the turn was also ingested as an event, so the turn and the memories it
    # produced can be traced to each other.
    event_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    # Memories retrieved to answer this turn — the citations behind what the agent said.
    retrieved_memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
