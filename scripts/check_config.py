#!/usr/bin/env python
"""Validate configuration and connectivity before starting a deployment.

    python scripts/check_config.py
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from common.settings import get_settings
from database.session import dispose_engine, session_scope
from nlp import LocalEmbedder, compose
from nlp.question import analyze as analyze_question


async def main() -> int:
    settings = get_settings()
    failures: list[str] = []

    print(f"environment        : {settings.app_env}")
    print("language engine    : deterministic (no external model provider)")
    print(f"embedding          : local lexical, {settings.embedding_dimensions} dimensions")

    for problem in settings.check_production_safety():
        failures.append(problem)

    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            has_vector = await session.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            )
        print("database           : ok")
        if not has_vector:
            failures.append("pgvector extension is not installed (CREATE EXTENSION vector).")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"database unreachable: {exc}")

    try:
        embedder = LocalEmbedder(settings=settings)
        vector = await embedder.embed_one("connectivity check")
        if len(vector) != settings.embedding_dimensions:
            failures.append("Embedder returned the wrong vector width.")
        print(f"embedder           : ok ({embedder.model})")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"embedder failed: {exc}")

    try:
        answer = compose(analysis=analyze_question("what problems do they have?"), memories=[])
        print(f"answer engine      : ok ({answer.strategy})")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"answer engine failed: {exc}")

    await dispose_engine()

    if failures:
        print("\nProblems found:")
        for failure in failures:
            print(f"  ✗ {failure}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
