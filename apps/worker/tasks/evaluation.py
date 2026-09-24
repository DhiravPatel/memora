"""Running an evaluation set in the worker, for sets too large to wait for."""

from __future__ import annotations

from typing import Any

from app.services.evaluation_service import EvaluationService
from common.logging import get_logger
from database.repositories import EvalRepository, ProjectRepository
from worker.tasks.context import embedder_for, worker_session

logger = get_logger(__name__)


async def run_evaluation(ctx: dict[str, Any], run_id: str) -> dict[str, Any]:
    async with worker_session() as session:
        run = await EvalRepository(session).get_run(run_id)
        if run is None:
            # Enqueued before the request that created it committed; arq retries.
            raise RuntimeError(f"evaluation run {run_id} not visible yet")
        if run.status not in ("queued", "running"):
            return {"run_id": run_id, "status": run.status}
        project = await ProjectRepository(session).get(run.project_id)
        if project is None:
            return {"run_id": run_id, "status": "project_missing"}
        service = EvaluationService(session, embedder_for(ctx))
        try:
            await service.execute(run, project)
        except Exception as exc:  # noqa: BLE001 - recorded on the run rather than lost
            logger.error("eval.run_failed", run_id=run_id, error=str(exc))
            await service.fail(run, str(exc))
        return {"run_id": run_id, "status": run.status, "metrics": run.metrics}
