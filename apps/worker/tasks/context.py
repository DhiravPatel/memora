"""Worker context helpers: shared providers and session handling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.settings import get_settings
from database.session import create_session_factory
from memory_engine import MemoryEngine
from memory_engine.protocols import Embedder
from nlp import LocalEmbedder


@asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    """A session that commits on success, rolls back on failure."""
    factory = create_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def embedder_for(ctx: dict[str, Any]) -> Embedder:
    """One embedder per worker process; it is stateless and deterministic."""
    embedder = ctx.get("embedder")
    if embedder is None:
        embedder = ctx["embedder"] = LocalEmbedder()
    return embedder


def engine_for(ctx: dict[str, Any], session: AsyncSession) -> MemoryEngine:
    return MemoryEngine(session=session, embedder=embedder_for(ctx), settings=get_settings())
