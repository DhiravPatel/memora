"""Memory worker.

Run with::

    python apps/worker/main.py
    # or: arq worker.main.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq import cron, run_worker
from arq.connections import RedisSettings

from common.logging import get_logger
from common.settings import get_settings
from database.session import dispose_engine
from nlp import LocalEmbedder
from worker.tasks import (
    apply_retention,
    backfill_memory_indexes,
    backfill_memory_indexes_all,
    check_key_rotation,
    close_idle_sessions,
    consolidate_customer_memories,
    deliver_webhooks,
    detect_drift,
    detect_drift_all,
    expire_agent_approvals,
    generate_embeddings,
    link_memories,
    link_project,
    mine_vocabulary,
    mine_vocabulary_all,
    process_event,
    purge_old_deliveries,
    reclassify_memories,
    refresh_customer_foresight,
    refresh_customer_states,
    refresh_customer_states_all,
    report_queue_depth,
    reprocess_customer,
    reprocess_event,
    retry_failed_events,
    run_evaluation,
    send_email,
    send_invitation_email,
    snapshot_signals,
    snapshot_signals_all,
    summarize_customer,
    summarize_project,
    sweep_pending_events,
    sweep_stale_goals,
    warm_context,
)

logger = get_logger(__name__)

MEMORY_QUEUE = "memory:queue"


async def startup(ctx: dict[str, Any]) -> None:
    from common.logging import configure_logging

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    problems = settings.check_production_safety()
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

    # One embedder per worker process, shared by every job.
    ctx["embedder"] = LocalEmbedder(settings=settings)
    logger.info(
        "worker.startup",
        engine="deterministic",
        embedding_model=ctx["embedder"].model,
    )


async def summarize_project_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly: refresh rolling summaries for every project."""
    from sqlalchemy import select

    from database.models import Project
    from worker.tasks.context import worker_session

    queued = 0
    async with worker_session() as session:
        project_ids = [row[0] for row in await session.execute(select(Project.id))]
    for project_id in project_ids:
        await ctx["redis"].enqueue_job("summarize_project", project_id)
        queued += 1
    return {"projects": queued}


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()
    logger.info("worker.shutdown")


class WorkerSettings:
    functions = [
        process_event,
        reprocess_event,
        reprocess_customer,
        retry_failed_events,
        generate_embeddings,
        consolidate_customer_memories,
        summarize_customer,
        summarize_project,
        link_memories,
        link_project,
        warm_context,
        deliver_webhooks,
        purge_old_deliveries,
        snapshot_signals,
        refresh_customer_foresight,
        mine_vocabulary,
        reclassify_memories,
        refresh_customer_states,
        run_evaluation,
        detect_drift,
        backfill_memory_indexes,
        check_key_rotation,
        send_email,
        send_invitation_email,
    ]
    cron_jobs = [
        # Webhook deliveries are drained every 30 seconds: fast enough to feel immediate,
        # slow enough that a dead receiver cannot spin the worker.
        cron(deliver_webhooks, second={0, 30}, run_at_startup=True),
        cron(sweep_pending_events, minute={0, 15, 30, 45}, run_at_startup=False),
        cron(apply_retention, hour=3, minute=15),
        cron(purge_old_deliveries, hour=3, minute=45),
        cron(summarize_project_all, hour=4, minute=0),
        # The forecast for a customer who has gone quiet only changes because time passed,
        # so it is recomputed nightly rather than waiting for an event that never comes.
        cron(snapshot_signals_all, hour=4, minute=30),
        cron(sweep_stale_goals, hour=5, minute=0),
        # Before the lifecycle (05:15), which can read drift facts; after signals and goals.
        cron(detect_drift_all, hour=5, minute=5),
        # After signals (04:30) and goals (05:00), because the lifecycle reads both.
        cron(refresh_customer_states_all, hour=5, minute=15),
        # After the summaries, so a project's newest memories are in the corpus.
        cron(mine_vocabulary_all, hour=5, minute=30),
        cron(close_idle_sessions, minute={10, 40}),
        cron(report_queue_depth, minute=set(range(0, 60, 5))),
        # Cheap when there is nothing to fill; catches up concepts and embeddings anything missed.
        cron(backfill_memory_indexes_all, hour=2, minute=30, run_at_startup=True),
        # Early, before anyone is awake to be annoyed, and after the night's other work.
        cron(check_key_rotation, hour=6, minute=0),
        # Every five minutes, so an agent waiting on a webhook hears "expired" promptly.
        cron(expire_agent_approvals, minute=set(range(2, 60, 5))),
    ]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = MEMORY_QUEUE
    max_jobs = 10
    job_timeout = 120
    max_tries = 3
    retry_jobs = True
    keep_result = 3600
    # arq reads this attribute directly when the worker starts.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)


if __name__ == "__main__":
    run_worker(WorkerSettings)  # type: ignore[arg-type]
