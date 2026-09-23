"""Links between memories: the causal and temporal structure of a customer's story."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import ID_LENGTH, Base


class MemoryLink(Base):
    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint(
            "source_memory_id", "target_memory_id", "link_type", name="uq_memory_links_edge"
        ),
        Index("ix_memory_links_customer", "customer_id", "link_type"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[str] = mapped_column(String(ID_LENGTH), index=True)
    source_memory_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    target_memory_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    # caused_by | resolved_by | relates_to | preceded_by
    link_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.5)
    # Why the engine drew this edge, in words, so a human can disagree with it.
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
