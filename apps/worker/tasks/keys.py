"""The nightly secrets-key rotation check.

``scripts/encrypt_secrets.py`` has always been able to rotate the key. What was missing was
anything that *noticed* it had not been run — so a deployment could sit on a two-year-old
key, or on a rotation that was started and never finished, and the first sign of either
would be an audit or an outage.

This runs once a day, records the age of the active key, and complains when it is past the
interval the deployment configured. A half-finished rotation is reported at the same time
and is the more urgent of the two: it means secrets are still readable only with a key
somebody is about to retire.
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from common.settings import get_settings
from database.rotation import rotation_status
from integrations.email import templates
from worker.tasks.context import worker_session

logger = get_logger(__name__)


async def check_key_rotation(ctx: dict[str, Any]) -> dict[str, Any]:
    """Report on the age of the secrets encryption key, and alert if it is overdue."""
    settings = get_settings()
    keys = settings.encryption_keys
    if not keys:
        # Nothing is encrypted, so there is nothing to rotate. Production refuses to start
        # in this state; elsewhere it is a legitimate choice and not worth a nightly noise.
        return {"status": "not_configured"}

    async with worker_session() as session:
        status = await rotation_status(
            session, active=keys[0], max_age_days=settings.key_rotation_max_age_days
        )

    if not status.needs_attention:
        logger.info("keys.rotation_ok", **status.as_dict())
        return {"status": "ok", **status.as_dict()}

    logger.warning(
        "keys.rotation_due",
        **status.as_dict(),
        reason="unfinished" if status.unfinished else "overdue",
    )

    if settings.ops_alert_email:
        subject, text, html = templates.key_rotation_due(
            environment=settings.app_env,
            age_days=status.age_days,
            threshold_days=status.max_age_days,
        )
        if status.unfinished:
            note = (
                f"\n{status.stale_secrets} secret(s) are still sealed under a previous key. "
                "Retiring that key before rewriting them would make them unreadable.\n"
            )
            text += note
            html = html.replace("</div>", f"<p><strong>{note.strip()}</strong></p></div>")
        # Through the worker's own queue so a dead relay retries on the ordinary schedule.
        await ctx["redis"].enqueue_job("send_email", settings.ops_alert_email, subject, text, html)

    return {"status": "attention", **status.as_dict()}
