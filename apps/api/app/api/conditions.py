"""The condition language, over HTTP (project API key).

The same language guardrails, the lifecycle, workflows and feature flags are written in,
exposed so a rule can be checked — and tried against a real customer — before it is saved
anywhere it would act.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import ApiProject, Clearance, DBSession
from app.schemas.state import (
    ConditionEvaluateIn,
    ConditionEvaluationOut,
    ConditionIn,
    ConditionValidation,
    FactCatalogOut,
)
from app.services import state_views
from common.errors import NotFoundError
from database.repositories import CustomerRepository

router = APIRouter(prefix="/v1/conditions", tags=["conditions"])


@router.get("/catalog", response_model=FactCatalogOut)
async def fact_catalog() -> FactCatalogOut:
    """Every fact a condition can read, its type, and the operators it accepts."""
    return state_views.catalog()


@router.post("/validate", response_model=ConditionValidation)
async def validate_condition(payload: ConditionIn) -> ConditionValidation:
    """Parse and check a condition without evaluating it.

    Always 200: `valid` says whether it compiled, and a parse error carries the character
    `position` it happened at, so an editor can underline it.
    """
    return state_views.validate(payload.condition)


@router.post("/evaluate", response_model=ConditionEvaluationOut)
async def evaluate_condition(
    payload: ConditionEvaluateIn, project: ApiProject, session: DBSession, cleared: Clearance
) -> ConditionEvaluationOut:
    """Evaluate a condition against one customer's facts, with the full trace.

    Evaluated on the facts *this caller* may see: without clearance, facts that came only
    from restricted memories read as unknown, so a condition cannot be used to probe one.
    """
    customer = await CustomerRepository(session).resolve(payload.customer_id, project.id)
    if customer is None:
        raise NotFoundError(f"Customer '{payload.customer_id}' not found.")
    return await state_views.evaluate(
        session, project=project, customer=customer, condition=payload.condition, cleared=cleared
    )
