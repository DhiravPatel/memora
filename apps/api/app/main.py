"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.core.middleware import register_middleware
from app.core.queue import close_queue
from common.logging import get_logger
from common.settings import get_settings
from database.session import dispose_engine
from nlp import LocalEmbedder

logger = get_logger(__name__)

DESCRIPTION = """
Persistent memory infrastructure for AI-powered SaaS.

Send events to `/v1/events`, then ask questions with `/v1/memory/query` or fetch
agent-ready context with `/v1/memory/context`.

Extraction, consolidation, retrieval and answering are fully deterministic: no LLM is
called, no customer text leaves the deployment, and the same history always produces the
same answer.

**Authentication**

* Customer applications send a project API key in the `X-API-Key` header.
* The dashboard sends a user JWT in the `Authorization: Bearer …` header.

Every request is scoped to exactly one project. Cross-tenant access is not possible.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()

    problems = settings.check_production_safety()
    if problems:
        # Refuse to start rather than run a production deployment with dev secrets.
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

    app.state.embedder = LocalEmbedder(settings=settings)
    logger.info(
        "api.startup",
        environment=settings.app_env,
        engine="deterministic",
        embedding_model=app.state.embedder.model,
        embedding_dimensions=settings.embedding_dimensions,
    )
    try:
        yield
    finally:
        await close_queue()
        await dispose_engine()
        logger.info("api.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Memory Layer",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )
    register_middleware(app)
    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": "AI Memory Layer", "version": "0.1.0", "docs": "/docs"}

    return app


app = create_app()
