"""Shared response shapes."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    data: list[T]
    total: int = 0
    limit: int = 50
    offset: int = 0
    # How many rows the caller's clearance hid. Zero for a cleared reader, and zero for
    # every page that has nothing to hide. Reporting it is deliberate: a reader who is
    # told "3 withheld" stops looking for a bug, and a reader who is told nothing goes
    # hunting for the memory they know exists.
    withheld: int = 0

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.data) < self.total


class Message(BaseModel):
    message: str


class DeletionResult(BaseModel):
    deleted: bool = True
    resource: str
    resource_id: str
    removed: dict[str, int] = Field(default_factory=dict)
