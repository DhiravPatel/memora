"""Structural types the engine depends on.

The engine needs *an embedder*, not a specific implementation: the default is the local
deterministic one, and anything that satisfies this protocol (a hosted model, a compiled
extension, a stub in tests) can be substituted without touching engine code.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Produces a fixed-width vector for a text."""

    name: str
    model: str
    dimensions: int

    async def embed_one(self, text: str) -> list[float]:
        ...

    async def embed(self, texts: Sequence[str]) -> object:
        ...
