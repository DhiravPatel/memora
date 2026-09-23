"""Vector embeddings for memories, stored in PostgreSQL via pgvector."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.settings import get_settings
from database.base import ID_LENGTH, Base

EMBEDDING_DIMENSIONS = get_settings().embedding_dimensions


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("memory_id", "model", name="uq_embeddings_memory_id_model"),
        # HNSW gives good recall/latency for cosine search without tuning list counts.
        Index(
            "ix_embeddings_vector",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    memory_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(nullable=False, default=EMBEDDING_DIMENSIONS)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
