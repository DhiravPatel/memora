"""A project's vocabulary: what it learned, and what it was told.

Two sources in one table. **Mined** rows are terms that keep turning up together in the
project's own memories; the nightly job rewrites them wholesale. **Curated** rows are
written by a person — the glossary that says "in our product the loader *is* the importer"
— and mining never touches them.

A person can also *reject* a mined pair. The rejection is kept as a row rather than a
deletion, because otherwise the next mining run would cheerfully learn it again.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.enums import VocabularySource, VocabularyStatus
from database.base import ID_LENGTH, Base


class LearnedTerm(Base):
    __tablename__ = "learned_terms"
    __table_args__ = (
        UniqueConstraint("project_id", "term", "synonym", name="uq_learned_terms_project_pair"),
        Index("ix_learned_terms_project_score", "project_id", "score"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(64), nullable=False)
    synonym: Mapped[str] = mapped_column(String(64), nullable=False)
    # Normalised PMI, 0-1: how much more often these two appear together than chance.
    # Curated rows carry 1.0 — a person asserting it is the strongest evidence there is.
    score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    # How many memories back the pair. The number somebody argues with. 0 when curated.
    support: Mapped[int] = mapped_column(nullable=False, default=0)
    source: Mapped[VocabularySource] = mapped_column(
        String(16), nullable=False, default=VocabularySource.MINED, index=True
    )
    status: Mapped[VocabularyStatus] = mapped_column(
        String(16), nullable=False, default=VocabularyStatus.ACTIVE
    )
    # Who took responsibility for a curated row or a rejection.
    decided_by: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    note: Mapped[str | None] = mapped_column(String(255))
    mined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
