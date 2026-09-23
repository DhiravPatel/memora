"""Background jobs."""

from worker.tasks.build_context import warm_context
from worker.tasks.consolidate_memory import consolidate_customer_memories
from worker.tasks.deliver_webhooks import deliver_webhooks, purge_old_deliveries
from worker.tasks.email import render_invitation, send_email, send_invitation_email
from worker.tasks.foresight import (
    close_idle_sessions,
    refresh_customer_foresight,
    snapshot_signals,
    snapshot_signals_all,
    sweep_stale_goals,
)
from worker.tasks.generate_embedding import generate_embeddings
from worker.tasks.keys import check_key_rotation
from worker.tasks.link_memories import link_memories, link_project
from worker.tasks.maintenance import apply_retention, report_queue_depth, sweep_pending_events
from worker.tasks.policy import reclassify_memories, reclassify_project
from worker.tasks.process_event import process_event
from worker.tasks.reprocess import reprocess_customer, reprocess_event, retry_failed_events
from worker.tasks.summarize import summarize_customer, summarize_project
from worker.tasks.vocabulary import (
    mine_project_vocabulary,
    mine_vocabulary,
    mine_vocabulary_all,
)

__all__ = [
    "apply_retention",
    "close_idle_sessions",
    "consolidate_customer_memories",
    "deliver_webhooks",
    "generate_embeddings",
    "link_memories",
    "link_project",
    "mine_project_vocabulary",
    "mine_vocabulary",
    "mine_vocabulary_all",
    "process_event",
    "check_key_rotation",
    "reclassify_memories",
    "render_invitation",
    "send_email",
    "send_invitation_email",
    "reclassify_project",
    "purge_old_deliveries",
    "report_queue_depth",
    "refresh_customer_foresight",
    "reprocess_customer",
    "reprocess_event",
    "retry_failed_events",
    "snapshot_signals",
    "snapshot_signals_all",
    "summarize_customer",
    "summarize_project",
    "sweep_pending_events",
    "sweep_stale_goals",
    "warm_context",
]
