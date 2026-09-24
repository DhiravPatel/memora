"""Memory evaluation (project API key).

Sets of cases whose right answers are known — questions retrieval should answer, and events
extraction should turn into the right memories — runs that measure them, regressions that
measure a settings change before it is saved, and the scorecard. Reads need `memory:read`;
creating sets, cases and runs needs `memory:write`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import ApiProject, Clearance, DBSession, EmbedderDep, require_scope
from app.schemas.common import Message
from app.schemas.evaluation import (
    EvalCaseOut,
    EvalCasesIn,
    EvalRunIn,
    EvalRunOut,
    EvalRunSummaryOut,
    EvalSetDetail,
    EvalSetIn,
    EvalSetOut,
    EvalSuggestion,
    RegressionIn,
    RegressionOut,
)
from app.services import evaluation_views as views
from app.services.evaluation_service import EvaluationService
from common.enums import ApiKeyScope

WRITE = [Depends(require_scope(ApiKeyScope.MEMORY_WRITE))]

router = APIRouter(prefix="/v1/evals", tags=["evaluation"])


@router.get("", response_model=list[EvalSetOut])
async def list_eval_sets(project: ApiProject, session: DBSession) -> list[EvalSetOut]:
    """Every evaluation set, with its case count and latest successful run."""
    return await views.list_sets(session, project=project)


@router.post("", response_model=EvalSetOut, status_code=201, dependencies=WRITE)
async def create_eval_set(payload: EvalSetIn, project: ApiProject, session: DBSession) -> EvalSetOut:
    return await views.create_set(
        session, project=project, name=payload.name, description=payload.description, actor_id=None
    )


@router.get("/suggestions", response_model=list[EvalSuggestion])
async def eval_suggestions(
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[EvalSuggestion]:
    """Recent real questions and what retrieval returned — pick the right answer to make a case."""
    return await views.suggestions(session, project=project, limit=limit, cleared=cleared)


@router.get("/scorecard")
async def eval_scorecard(project: ApiProject, session: DBSession, cleared: Clearance) -> dict:
    """Memory quality in one place: retrieval recall, MRR and citation accuracy; extraction
    accuracy, false-memory rate and type, sensitivity and consolidation accuracy — from the
    latest run of every set — with duplicate control and consistency from the quality report."""
    return await views.scorecard(session, project=project, cleared=cleared)


@router.get("/runs/{run_id}", response_model=EvalRunOut)
async def get_eval_run(
    run_id: str, project: ApiProject, session: DBSession, cleared: Clearance
) -> EvalRunOut:
    """A run with its per-question results and the comparison with the previous run."""
    return await views.get_run(session, project=project, run_id=run_id, cleared=cleared)


@router.get("/{set_id}", response_model=EvalSetDetail)
async def get_eval_set(set_id: str, project: ApiProject, session: DBSession) -> EvalSetDetail:
    return await views.set_detail(session, project=project, set_id=set_id)


@router.delete("/{set_id}", response_model=Message, dependencies=WRITE)
async def delete_eval_set(set_id: str, project: ApiProject, session: DBSession) -> Message:
    await EvaluationService(session).delete_set(
        project=project, set_id=set_id, actor_type="api_key", actor_id=project.id
    )
    return Message(message="Evaluation set deleted.")


@router.post("/{set_id}/cases", response_model=list[EvalCaseOut], status_code=201, dependencies=WRITE)
async def add_eval_cases(
    set_id: str, payload: EvalCasesIn, project: ApiProject, session: DBSession, cleared: Clearance
) -> list[EvalCaseOut]:
    """Add up to 200 cases at once. Each says what the right answer is — memory ids, phrases, or both."""
    return await views.add_cases(session, project=project, set_id=set_id, cases=payload.cases, cleared=cleared)


@router.delete("/{set_id}/cases/{case_id}", response_model=Message, dependencies=WRITE)
async def delete_eval_case(set_id: str, case_id: str, project: ApiProject, session: DBSession) -> Message:
    service = EvaluationService(session)
    row = await service.get_set(project=project, set_id=set_id)
    await service.delete_case(project=project, eval_set=row, case_id=case_id)
    return Message(message="Case deleted.")


@router.get("/{set_id}/runs", response_model=list[EvalRunSummaryOut])
async def list_eval_runs(set_id: str, project: ApiProject, session: DBSession) -> list[EvalRunSummaryOut]:
    return await views.list_runs(session, project=project, set_id=set_id)


@router.post("/{set_id}/runs", response_model=EvalRunOut, status_code=201, dependencies=WRITE)
async def start_eval_run(
    set_id: str,
    payload: EvalRunIn,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    embedder: EmbedderDep,
) -> EvalRunOut:
    """Run the set. With `wait` (default) and at most 100 cases the result comes back here;
    otherwise the run is queued and `GET /v1/evals/runs/{id}` reports on it."""
    return await views.start_run(
        session, project=project, set_id=set_id, payload=payload, cleared=cleared, actor_id=None, embedder=embedder
    )


@router.post("/{set_id}/regression", response_model=RegressionOut, dependencies=WRITE)
async def eval_regression(
    set_id: str,
    payload: RegressionIn,
    project: ApiProject,
    session: DBSession,
    cleared: Clearance,
    embedder: EmbedderDep,
) -> RegressionOut:
    """Before changing a setting: run the set as configured and under the proposed settings
    (validated like a save, never saved) and list every case the change would break."""
    return await views.regression(
        session, project=project, set_id=set_id, payload=payload, cleared=cleared, actor_id=None, embedder=embedder
    )
