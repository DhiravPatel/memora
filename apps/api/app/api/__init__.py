"""HTTP routers."""

from fastapi import APIRouter, Depends

from app.api import (
    admin,
    agent,
    agents_dashboard,
    auth,
    conditions,
    context,
    customers,
    dashboard,
    drift,
    evals,
    events,
    goals,
    health,
    integrations,
    lifecycle,
    memories,
    projects,
    quality,
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
api_router.include_router(agents_dashboard.router)

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
    drift.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
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
# Conditions read the fact document, which is derived from memory.
api_router.include_router(
    conditions.router,
    dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))],
)
api_router.include_router(
    evals.router,
    dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))],
)
api_router.include_router(
    quality.router,
    dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))],
)
api_router.include_router(
    lifecycle.router,
    dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.CUSTOMERS_READ))],
)
# Agents read memory to brief themselves and to be told what they may do; session writes
# additionally require memory:write, profile edits admin, and approval decisions
# approvals:decide.
api_router.include_router(
    agent.router, dependencies=[*rate_limited, Depends(require_scope(ApiKeyScope.MEMORY_READ))]
)

__all__ = ["api_router"]
