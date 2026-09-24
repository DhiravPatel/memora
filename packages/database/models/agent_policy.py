"""Agent profiles, the guardrail checks agents ask for, and the approvals that follow.

A profile is what an agent is *for*: which kinds of memory its job needs, whether it may
ever read restricted ones, and which actions it may take. It is bound to an API key, so the
agent cannot choose a more generous one by asking.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base, TimestampMixin


class AgentProfile(Base, TimestampMixin):
    __tablename__ = "agent_profiles"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_agent_profiles_project_name"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Memory types this agent may read. Empty means all of them.
    readable_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Restricted memories need the key's memory:restricted scope *and* this.
    can_read_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Empty means any action not denied.
    allowed_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    denied_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class AgentCheck(Base):
    __tablename__ = "agent_checks"
    __table_args__ = (
        Index("ix_agent_checks_project_created", "project_id", "created_at"),
        Index("ix_agent_checks_customer_created", "customer_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[str | None] = mapped_column(String(120))
    api_key_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # allow | require_approval | deny
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    approval_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    snapshot_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentApproval(Base):
    __tablename__ = "agent_approvals"
    __table_args__ = (Index("ix_agent_approvals_project_status", "project_id", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    agent: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # pending | approved | rejected | expired | used
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An approval is for one action, once: redeemed by the check that carries its id.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentAction(Base):
    """An action an agent asked to take, through the gateway (§26 4.5), from request to done.

    A check answers "may I?"; an action is the thing itself, with a life: requested, then
    ``allowed``, ``pending_approval`` or ``denied``; an allowed one ends ``done``, ``failed``
    or ``cancelled`` when the agent reports back. What was allowed or done is the customer's
    action history — ``actions.*`` facts — which is how "a third credit this month needs a
    person" can be a rule.
    """

    __tablename__ = "agent_actions"
    __table_args__ = (
        Index("ix_agent_actions_customer_action_created", "customer_id", "action", "created_at"),
        Index("ix_agent_actions_project_created", "project_id", "created_at"),
        # Retrying a request with the same key returns the same action.
        UniqueConstraint("project_id", "idempotency_key", name="uq_agent_actions_project_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # request.amount, kept as a column so history can sum it.
    amount: Mapped[float | None] = mapped_column()
    # allowed | pending_approval | denied | done | failed | cancelled | expired
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    check_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    approval_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    agent: Mapped[str | None] = mapped_column(String(120))
    api_key_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    session_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    outcome_note: Mapped[str | None] = mapped_column(Text)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
