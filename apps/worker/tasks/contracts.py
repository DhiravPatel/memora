"""The hourly memory-contract digest (§26 7.1).

A contract in ``warn`` mode keeps the events that break it; one in ``enforce`` mode refuses
them. Either way somebody should hear about it without watching a dashboard: once an hour,
each contract with new violations sends ``event.contract_violated`` — how many events broke it
since the last digest, how many were refused, and the commonest ways — then remembers how far
it reported.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from common.logging import get_logger
from common.time import utcnow
from database.models import EventContract
from database.repositories import ContractRepository
from webhooks import WebhookDispatcher, event_contract_violated
from worker.tasks.context import worker_session

logger = get_logger(__name__)
TOP = 5


async def report_contract_violations(ctx: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    reported = scanned = 0
    async with worker_session() as session:
        contracts = list(
            (await session.execute(select(EventContract).where(EventContract.mode != "off"))).scalars()
        )
        repository = ContractRepository(session)
        for contract in contracts:
            scanned += 1
            since = contract.last_reported_at or contract.created_at
            violating = await repository.violations_since(
                project_id=contract.project_id, event_type=contract.event_type, since=since
            )
            refused = (contract.rejected_count or 0) - (contract.reported_rejected_count or 0)
            if violating or refused:
                report = await repository.report(project_id=contract.project_id, event_type=contract.event_type, since=since)
                await WebhookDispatcher(session).emit(
                    event_contract_violated(
                        project_id=contract.project_id,
                        event_type=contract.event_type,
                        contract_version=contract.version,
                        mode=contract.mode,
                        since=since,
                        until=now,
                        violating_events=violating,
                        refused_events=max(0, refused),
                        violations=[
                            {key: item[key] for key in ("path", "rule", "expected", "received", "events")}
                            for item in report["violations"][:TOP]
                        ],
                    )
                )
                reported += 1
            contract.last_reported_at = now
            contract.reported_rejected_count = contract.rejected_count or 0
        await session.commit()
    if reported:
        logger.info("contract.violations_reported", contracts=reported)
    return {"scanned": scanned, "reported": reported}
