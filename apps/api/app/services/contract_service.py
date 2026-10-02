"""Memory contracts: managed, checked on ingest, reported (§26 7.1).

A contract is compiled when it is saved — a bad regular expression or an unknown type is a
422 then, not an exception on every event later — and stored in its canonical form with a
version that moves on every change. Each event of a contracted type is checked as it is
received; the check, with the contract version, is stored on the event. ``enforce`` refuses
a violating event (``422 contract_violation``) and counts the refusal on the contract, since a
refused event is never stored; ``warn`` keeps it; ``off`` checks nothing but still applies the
contract's text field and importance.

Integration webhooks are never refused — the provider would retry for ever — so for them an
enforcing contract behaves like a warning one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import AuditAction
from common.errors import ContractViolationError, NotFoundError, ValidationError
from common.logging import get_logger
from common.time import utcnow
from database.models import EventContract, Project
from database.repositories import AuditRepository, ContractRepository
from memory_engine.contracts import (
    Contract,
    ContractError,
    Violation,
    compile_contract,
    infer,
    outcome,
    preview,
    validate,
)

logger = get_logger(__name__)

DEFAULT_WINDOW = timedelta(days=7)
MAX_WINDOW_DAYS = 90
MAX_YAML = 20_000


@dataclass(slots=True)
class ContractCheck:
    row: EventContract
    contract: Contract
    violations: list[Violation]
    enforced: bool

    @property
    def refuses(self) -> bool:
        return self.enforced and self.contract.mode == "enforce" and bool(self.violations)

    def stored(self) -> dict[str, Any]:
        """What is kept on the event."""
        return outcome(self.contract, self.violations, version=self.row.version)


def parse(raw: dict[str, Any] | None, yaml_text: str | None) -> dict[str, Any]:
    """A contract as an object — or written in YAML, as people write it."""
    if yaml_text is not None:
        if len(yaml_text) > MAX_YAML:
            raise ValidationError(f"A contract in YAML is at most {MAX_YAML} characters.")
        try:
            loaded = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise ValidationError(f"The YAML does not parse: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValidationError("The YAML must describe one contract — an object with event, required, fields….")
        return loaded
    return dict(raw or {})


def contract_out(row: EventContract) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "mode": row.mode,
        "version": row.version,
        "definition": row.definition,
        "rejected": {
            "count": row.rejected_count or 0,
            "last_at": row.last_rejected_at,
            "recent": list(row.recent_rejections or []),
        },
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
    }


def window(since: str | None) -> datetime:
    """'7d', '24h', '30d' — how far back a report reads. Default a week."""
    text = (since or "").strip().lower()
    if not text:
        return utcnow() - DEFAULT_WINDOW
    unit = text[-1:]
    try:
        amount = int(text[:-1])
    except ValueError as exc:
        raise ValidationError("'since' is a span such as 24h, 7d or 30d.") from exc
    delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount)}.get(unit)
    if delta is None or amount < 1 or delta > timedelta(days=MAX_WINDOW_DAYS):
        raise ValidationError(f"'since' is a span such as 24h, 7d or 30d, at most {MAX_WINDOW_DAYS} days.")
    return utcnow() - delta


class ContractService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.contracts = ContractRepository(session)

    # ------------------------------------------------------------------ ingest

    async def compiled(self, *, project_id: str, event_type: str, row: EventContract | None = None) -> tuple[EventContract, Contract] | None:
        row = row or await self.contracts.get(project_id=project_id, event_type=event_type)
        if row is None:
            return None
        try:
            return row, compile_contract(row.definition, event_type=row.event_type)
        except ContractError as exc:
            # Compiled when saved, so this means it was written around the API.
            logger.error("contract.invalid", project_id=project_id, event_type=event_type, error=str(exc))
            return None

    async def check(
        self,
        *,
        project: Project,
        event_type: str,
        data: dict[str, Any],
        enforced: bool = True,
        preloaded: dict[str, EventContract] | None = None,
    ) -> ContractCheck | None:
        """Check a payload against its type's contract, if there is one."""
        row = preloaded.get(event_type) if preloaded is not None else None
        if preloaded is not None and row is None:
            return None
        found = await self.compiled(project_id=project.id, event_type=event_type, row=row)
        if found is None:
            return None
        row, contract = found
        violations = validate(contract, data) if contract.checks else []
        return ContractCheck(row, contract, violations, enforced)

    async def refuse(self, check: ContractCheck, *, external_event_id: str | None, customer_id: str) -> None:
        """Count a refusal on the contract — the event itself is not stored."""
        await self.contracts.record_rejection(
            check.row,
            {
                "at": utcnow().isoformat(),
                "external_event_id": external_event_id,
                "customer_id": customer_id,
                "violations": [violation.as_dict() for violation in check.violations][:10],
            },
        )
        logger.info(
            "contract.refused",
            project_id=check.row.project_id,
            event_type=check.row.event_type,
            version=check.row.version,
            violations=len(check.violations),
        )

    @staticmethod
    def error(check: ContractCheck) -> ContractViolationError:
        first = check.violations[0].message
        more = len(check.violations) - 1
        return ContractViolationError(
            f"This {check.row.event_type} event breaks its contract: {first}" + (f" (and {more} more)" if more else ""),
            details={
                "event_type": check.row.event_type,
                "contract_version": check.row.version,
                "violations": [violation.as_dict() for violation in check.violations],
            },
        )

    # ------------------------------------------------------------------ manage

    async def list(self, *, project: Project, since: str | None = None) -> list[dict[str, Any]]:
        start = window(since)
        coverage = {item["event_type"]: item for item in await self.contracts.coverage(project_id=project.id, since=start)}
        found = []
        for row in await self.contracts.list(project_id=project.id):
            seen = coverage.get(row.event_type) or {}
            found.append({**contract_out(row), "events": seen.get("events", 0), "violating": seen.get("violating", 0)})
        return found

    async def get(self, *, project: Project, event_type: str, since: str | None = None) -> dict[str, Any]:
        row = await self._row(project, event_type)
        return {
            **contract_out(row),
            "report": await self.contracts.report(project_id=project.id, event_type=row.event_type, since=window(since)),
        }

    async def save(
        self,
        *,
        project: Project,
        raw: dict[str, Any] | None,
        yaml_text: str | None = None,
        event_type: str | None = None,
        actor_type: str,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """Create or replace a contract. Compiled first — refused whole if any part is wrong.
        ``created`` says whether there was none before."""
        try:
            contract = compile_contract(parse(raw, yaml_text), event_type=event_type)
        except ContractError as exc:
            raise ValidationError(str(exc)) from exc
        definition = contract.as_dict()
        row, previous = await self.contracts.save(
            project_id=project.id, event_type=contract.event_type, definition=definition, actor=actor_id
        )
        if previous != definition:
            await AuditRepository(self.session).record(
                action=AuditAction.CONFIGURATION_CHANGE,
                actor_type=actor_type,
                actor_id=actor_id,
                organization_id=project.organization_id,
                project_id=project.id,
                resource_type="event_contract",
                resource_id=row.id,
                metadata={"event_type": row.event_type, "version": row.version, "before": previous, "after": definition},
            )
            logger.info("contract.saved", project_id=project.id, event_type=row.event_type, version=row.version, mode=row.mode)
        return {**contract_out(row), "created": previous is None}

    async def delete(self, *, project: Project, event_type: str, actor_type: str, actor_id: str | None) -> None:
        row = await self._row(project, event_type)
        await AuditRepository(self.session).record(
            action=AuditAction.CONFIGURATION_CHANGE,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=project.organization_id,
            project_id=project.id,
            resource_type="event_contract",
            resource_id=row.id,
            metadata={"event_type": row.event_type, "version": row.version, "deleted": row.definition},
        )
        await self.contracts.delete(row)

    async def test(
        self,
        *,
        project: Project,
        event_type: str,
        data: dict[str, Any],
        raw: dict[str, Any] | None = None,
        yaml_text: str | None = None,
    ) -> dict[str, Any]:
        """Check a payload against the saved contract — or against a proposed one."""
        if raw is not None or yaml_text is not None:
            try:
                contract = compile_contract(parse(raw, yaml_text), event_type=event_type)
            except ContractError as exc:
                raise ValidationError(str(exc)) from exc
            version = None
        else:
            found = await self.compiled(project_id=project.id, event_type=event_type)
            if found is None:
                raise NotFoundError(f"No contract for '{event_type}'.")
            row, contract = found
            version = row.version
        violations = validate(contract, data)
        return {
            "event_type": event_type,
            "version": version,
            "mode": contract.mode,
            "valid": not violations,
            "would_refuse": contract.mode == "enforce" and bool(violations),
            "violations": [violation.as_dict() for violation in violations],
            "text": preview(_text(contract, data)) if contract.text_field else None,
            "definition": contract.as_dict(),
        }

    async def draft(self, *, project: Project, event_type: str, limit: int = 200) -> dict[str, Any]:
        """A contract inferred from the type's recent payloads — to read, adjust and save."""
        samples = await self.contracts.samples(project_id=project.id, event_type=event_type, limit=max(1, min(limit, 1000)))
        if not samples:
            raise NotFoundError(f"No '{event_type}' events received yet to learn a contract from.")
        return infer(event_type, samples)

    async def coverage(self, *, project: Project, since: str | None = None) -> list[dict[str, Any]]:
        """Every event type received, and whether a contract covers it."""
        rows = await self.contracts.coverage(project_id=project.id, since=window(since))
        contracts = {row.event_type: row for row in await self.contracts.list(project_id=project.id)}
        found = []
        for item in rows:
            row = contracts.get(item["event_type"])
            found.append({**item, "mode": row.mode if row else None, "version": row.version if row else None})
        for name, row in contracts.items():
            if not any(item["event_type"] == name for item in rows):
                found.append({"event_type": name, "events": 0, "violating": 0, "last_seen_at": None, "mode": row.mode, "version": row.version})
        return found

    async def _row(self, project: Project, event_type: str) -> EventContract:
        row = await self.contracts.get(project_id=project.id, event_type=event_type.strip().lower())
        if row is None:
            raise NotFoundError(f"No contract for '{event_type}'.")
        return row


def _text(contract: Contract, data: dict[str, Any]) -> Any:
    from memory_engine.contracts import value_at

    return value_at(data, contract.text_field or "")


__all__ = ["ContractCheck", "ContractService", "contract_out", "parse", "window"]
