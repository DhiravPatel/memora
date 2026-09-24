"""Evaluation, shaped for a reader — shared by the API-key and dashboard routes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue import enqueue
from app.schemas.evaluation import (
    EvalCaseIn,
    EvalCaseOut,
    EvalRunIn,
    EvalRunOut,
    EvalRunSummaryOut,
    EvalSetDetail,
    EvalSetOut,
    EvalSuggestion,
    RegressionIn,
    RegressionOut,
)
from app.services.evaluation_service import INLINE_LIMIT, CaseInput, EvaluationService
from app.services.reader import Reader
from common.errors import NotFoundError
from database.models import EvalCase, EvalRun, EvalSet, Project
from database.repositories import CustomerRepository, EvalRepository
from memory_engine.policy import WITHHELD
from memory_engine.protocols import Embedder


def run_summary(run: EvalRun) -> EvalRunSummaryOut:
    return EvalRunSummaryOut(
        id=run.id,
        set_id=run.set_id,
        status=run.status,
        label=run.label,
        k=run.k,
        proposed=run.overrides is not None,
        metrics=run.metrics or {},
        comparison=run.comparison,
        error=run.error,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def run_out(run: EvalRun, visible: set[str] | None = None) -> EvalRunOut:
    """``visible`` is the set of retrieved memory ids the reader may see, or ``None`` for
    a reader who sees everything. A run stores the snippets it retrieved as whoever ran
    it saw them; anyone else gets the scores and the ids, and ``[withheld]`` for words."""
    results = run.results or []
    if visible is not None:
        results = [_shaped_result(result, visible) for result in results]
    return EvalRunOut(
        **run_summary(run).model_dump(),
        settings=run.settings or {},
        results=results,
        baseline_run_id=run.baseline_run_id,
        overrides=run.overrides,
    )


def _shaped_result(result: dict[str, Any], visible: set[str]) -> dict[str, Any]:
    retrieved = []
    hidden_any = False
    for item in result.get("retrieved") or []:
        if item.get("id") in visible:
            retrieved.append(item)
        else:
            hidden_any = True
            retrieved.append({**item, "content": WITHHELD})
    shaped = {**result, "retrieved": retrieved}
    if hidden_any and shaped.get("answer"):
        shaped["answer"] = WITHHELD
    return shaped


async def _visible_for_run(session: AsyncSession, project: Project, run: EvalRun, cleared: bool) -> set[str] | None:
    ids = {item.get("id") for result in run.results or [] for item in result.get("retrieved") or []}
    return await Reader(session, cleared=cleared).visible_memory_ids(project.id, {i for i in ids if i})


async def _cases_out(session: AsyncSession, project: Project, cases: list[EvalCase]) -> list[EvalCaseOut]:
    customers = await CustomerRepository(session).get_many(list({case.customer_id for case in cases}), project.id)
    external = {customer.id: customer.external_id for customer in customers}
    return [
        EvalCaseOut(
            id=case.id,
            customer_id=external.get(case.customer_id, case.customer_id),
            kind=case.kind or "retrieval",
            question=case.question,
            expected_memory_ids=list(case.expected_memory_ids or []),
            expected_phrases=list(case.expected_phrases or []),
            event=case.event,
            expectations=case.expectations,
            notes=case.notes,
            source=case.source,
            created_at=case.created_at,
        )
        for case in cases
    ]


async def list_sets(session: AsyncSession, *, project: Project) -> list[EvalSetOut]:
    repo = EvalRepository(session)
    sets = await repo.list_sets(project.id)
    counts = await repo.case_counts([row.id for row in sets])
    latest = await repo.latest_by_set([row.id for row in sets])
    return [
        EvalSetOut(
            id=row.id,
            name=row.name,
            description=row.description,
            cases=counts.get(row.id, 0),
            latest_run=run_summary(latest[row.id]) if row.id in latest else None,
            created_at=row.created_at,
        )
        for row in sets
    ]


async def create_set(
    session: AsyncSession, *, project: Project, name: str, description: str | None, actor_id: str | None
) -> EvalSetOut:
    row = await EvaluationService(session).create_set(
        project=project, name=name, description=description, actor_id=actor_id
    )
    return EvalSetOut(id=row.id, name=row.name, description=row.description, created_at=row.created_at)


async def set_detail(session: AsyncSession, *, project: Project, set_id: str) -> EvalSetDetail:
    service = EvaluationService(session)
    row = await service.get_set(project=project, set_id=set_id)
    repo = EvalRepository(session)
    cases = await repo.cases(row.id, project.id)
    runs = await repo.runs(row.id, project.id)
    latest = next((run for run in runs if run.status == "succeeded"), None)
    return EvalSetDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        cases=len(cases),
        latest_run=run_summary(latest) if latest else None,
        created_at=row.created_at,
        case_list=await _cases_out(session, project, cases),
        runs=[run_summary(run) for run in runs],
    )


async def add_cases(
    session: AsyncSession, *, project: Project, set_id: str, cases: list[EvalCaseIn], cleared: bool
) -> list[EvalCaseOut]:
    service = EvaluationService(session)
    row: EvalSet = await service.get_set(project=project, set_id=set_id)
    created = await service.add_cases(
        project=project,
        eval_set=row,
        cleared=cleared,
        cases=[
            CaseInput(
                customer_id=case.customer_id,
                question=case.question,
                expected_memory_ids=case.expected_memory_ids,
                expected_phrases=case.expected_phrases,
                notes=case.notes,
                kind=case.kind,
                event=case.event.model_dump() if case.event else None,
                expect=[item.model_dump(exclude_none=True) for item in case.expect],
                forbid=[item.model_dump(exclude_none=True) for item in case.forbid],
                expect_nothing=case.expect_nothing,
            )
            for case in cases
        ],
    )
    return await _cases_out(session, project, created)


async def start_run(
    session: AsyncSession,
    *,
    project: Project,
    set_id: str,
    payload: EvalRunIn,
    cleared: bool,
    actor_id: str | None,
    embedder: Embedder,
) -> EvalRunOut:
    service = EvaluationService(session, embedder)
    row = await service.get_set(project=project, set_id=set_id)
    run = await service.start_run(
        project=project, eval_set=row, label=payload.label, cleared=cleared, k=payload.k, actor_id=actor_id
    )
    size = len(await EvalRepository(session).cases(row.id, project.id))
    if payload.wait and size <= INLINE_LIMIT:
        await service.execute(run, project)
        return run_out(run, await _visible_for_run(session, project, run, cleared))

    # Committed before enqueueing would be ideal; the worker retries a run it cannot find
    # yet, and the dependency commits this session as the request ends.
    queued = await enqueue("run_evaluation", run.id)
    if queued is None:
        if size <= INLINE_LIMIT:
            await service.execute(run, project)
        else:
            await service.fail(run, "The worker queue is unavailable; try again shortly.")
    return run_out(run, await _visible_for_run(session, project, run, cleared))


async def get_run(session: AsyncSession, *, project: Project, run_id: str, cleared: bool) -> EvalRunOut:
    run = await EvalRepository(session).get_run(run_id, project.id)
    if run is None:
        raise NotFoundError("Evaluation run not found.")
    return run_out(run, await _visible_for_run(session, project, run, cleared))


async def list_runs(session: AsyncSession, *, project: Project, set_id: str) -> list[EvalRunSummaryOut]:
    row = await EvaluationService(session).get_set(project=project, set_id=set_id)
    return [run_summary(run) for run in await EvalRepository(session).runs(row.id, project.id)]


async def suggestions(
    session: AsyncSession, *, project: Project, limit: int, cleared: bool
) -> list[EvalSuggestion]:
    rows = await EvaluationService(session).suggestions(project=project, limit=limit, cleared=cleared)
    return [EvalSuggestion(**item) for item in rows]


async def regression(
    session: AsyncSession,
    *,
    project: Project,
    set_id: str,
    payload: RegressionIn,
    cleared: bool,
    actor_id: str | None,
    embedder: Embedder,
) -> RegressionOut:
    service = EvaluationService(session, embedder)
    row = await service.get_set(project=project, set_id=set_id)
    outcome = await service.regression(
        project=project, eval_set=row, settings=payload.settings, cleared=cleared, k=payload.k, actor_id=actor_id
    )
    return RegressionOut(
        safe=outcome.safe,
        summary=outcome.summary,
        newly_failing=outcome.newly_failing,
        newly_passing=outcome.newly_passing,
        current=run_out(outcome.current, await _visible_for_run(session, project, outcome.current, cleared)),
        proposed=run_out(outcome.proposed, await _visible_for_run(session, project, outcome.proposed, cleared)),
    )


async def scorecard(session: AsyncSession, *, project: Project, cleared: bool) -> dict[str, Any]:
    return await EvaluationService(session).scorecard(project=project, cleared=cleared)
