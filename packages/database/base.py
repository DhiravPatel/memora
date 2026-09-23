"""Declarative base and shared column mixins."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID_LENGTH = 64


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Server-generated values (``created_at``/``updated_at``) are fetched with RETURNING
    # during flush. Without this, reading ``updated_at`` after an UPDATE triggers a lazy
    # refresh, which raises MissingGreenlet under the async driver.
    __mapper_args__ = {"eager_defaults": True}

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


def id_column(prefix: str) -> Mapped[str]:
    """Primary key column holding a prefixed identifier (e.g. ``mem_…``)."""
    return mapped_column(String(ID_LENGTH), primary_key=True, comment=f"{prefix}_ prefixed id")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
