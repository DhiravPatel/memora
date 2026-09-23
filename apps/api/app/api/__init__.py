"""HTTP routers."""

from fastapi import APIRouter, Depends

from app.api import (
    admin,
    agent,
    auth,
    context,
    customers,
    dashboard,
    events,
    goals,
    health,
    integrations,
    memories,
    projects,
    query,
    stream,
    team,
)
from app.core.dependencies import enforce_rate_limit, require_scope
from common.enums import ApiKeyScope

api_router = APIRouter()

# Unauthenticated / user-authenticated surfaces.
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(dashboard.router)
api_router.include_router(stream.router)
api_router.include_router(admin.router)
api_router.include_router(team.router)

# Project API-key surfaces: rate limited per project and least-privilege by scope.
# Routers declare the *minimum* scope; individual write routes require more (see each module).
rate_limited = [Depends(enforce_rate_limit)]
api_router.include_router(
    events.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.EVENTS_WRITE))]
)
api_router.include_router(
    customers.router,
    dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.CUSTOMERS_READ))],
)
api_router.include_router(
    memories.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
)
api_router.include_router(
    query.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
)
api_router.include_router(
    context.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
)
api_router.include_router(
    integrations.router,
    dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.EVENTS_WRITE))],
)
api_router.include_router(
    goals.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
)
# Agent sessions read memory to brief the agent and write it back on close; the write
# routes on the router additionally require memory:write.
api_router.include_router(
    agent.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
)

__all__ = ["api_router"]
