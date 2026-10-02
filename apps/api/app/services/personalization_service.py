"""Personalization: computed when what it reads moves, stored, and served fast (§26 6.6).

A product reads a customer's personalization on every page load, so it is computed when its
inputs change rather than on every read: at the end of each state refresh — which follows
every processed event, runs nightly, and follows a confirmed drift or a person moving the
lifecycle — and stored per customer. A read serves the stored document, recomputing only
when there is none, the project's settings changed since, or it is older than a night.

Computed over what an *uncleared* reader may see: restricted memories never reach a
document that drives a product's UI. Guardrail verdicts are the exception that proves the
rule — decided over everything, like an agent's check, and explained without content.

When what the product sees changes, ``customer.personalization_changed`` says what moved.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.facts_service import FactsService
from app.services.guardrail_service import configuration_for, summary_of
from app.services.reader import Reader
from app.services.settings_service import effective
from app.services.usage import feature_usage
from common.enums import MemoryType
from common.errors import ValidationError
from common.logging import get_logger
from common.time import utcnow
from database.access import UNRESTRICTED, access_scope
from database.models import Customer, CustomerPersonalization, Project
from database.repositories import (
    CustomerRepository,
    EntityRepository,
    GoalRepository,
    MemoryRepository,
    PersonalizationRepository,
)
from memory_engine.facts import CustomerFacts
from memory_engine.guardrails import check as run_rules
from memory_engine.personalization import (
    PersonalizationError,
    PersonalizationInputs,
    Rules,
    Usage,
    changes,
    compile_rules,
    compute,
    fingerprint,
    stale,
)
from webhooks import WebhookDispatcher, customer_personalization_changed

logger = get_logger(__name__)

MAX_BATCH = 50
LIVE_GOALS = ("open", "progressing", "stalled")


def rules_for(project: Project) -> Rules:
    """The project's rules over the defaults. Validated when saved; a stored set that no
    longer compiles (written around the API) falls back to the defaults, loudly."""
    try:
        return compile_rules(effective(project).get("personalization") or {})
    except PersonalizationError as exc:
        logger.error("personalization.rules_invalid", project_id=project.id, error=str(exc))
        return compile_rules({})


# Bumped whenever how personalization is computed changes, so a deploy recomputes every stored
# document on its next read rather than serving yesterday's logic for up to a day.
ENGINE_VERSION = "2026-10-02.1"


def settings_hash(project: Project) -> str:
    """What a stored document was computed under: the project's settings and the engine."""
    encoded = json.dumps({"engine": ENGINE_VERSION, "settings": project.settings or {}}, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:32]


def flat(document: dict[str, Any]) -> dict[str, Any]:
    """The document without its details — what a webhook carries and a page caches."""
    return {key: value for key, value in document.items() if key != "details"}


class PersonalizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.store = PersonalizationRepository(session)

    # ------------------------------------------------------------------ compute

    async def compute(
        self,
        *,
        project: Project,
        customer: Customer,
        facts: CustomerFacts | None = None,
        rules: Rules | None = None,
    ) -> dict[str, Any]:
        """The document for one customer. ``facts`` is the full fact document when the
        caller already has one (a state refresh does); ``rules`` are proposed rules to try
        instead of the project's."""
        now = utcnow()
        rules = rules or rules_for(project)
        if facts is None:
            facts = await FactsService(self.session).for_customer(project=project, customer=customer)
        visible = facts.redacted()

        guardrails, _ = configuration_for(project)
        verdicts: dict[str, dict[str, Any]] = {}
        for action in rules.guardrail_actions():
            # Each check writes the proposed action into its documents: give it copies.
            full, shown = _copy(facts), _copy(visible)
            verdict = run_rules(
                action=action, request={}, facts=full, visible=shown, guardrails=guardrails, sanitize=True
            )
            reasons = [reason.as_dict() for reason in verdict.reasons]
            verdicts[action] = {"decision": verdict.decision, "summary": summary_of(verdict.decision, reasons)}

        # What an uncleared reader may see, whatever profile the caller is bound to: the
        # stored document is the same for every reader.
        with access_scope(UNRESTRICTED):
            memories = MemoryRepository(self.session, cleared=False)
            grouped = await memories.top_by_type(project_id=project.id, customer_id=customer.id, per_type=40)
            goals, _ = await GoalRepository(self.session).list(project_id=project.id, customer_id=customer.id, limit=50)
            live = [goal for goal in goals if str(goal.status) in LIVE_GOALS]
            hidden_goals = await Reader(self.session, cleared=False).hidden(project.id, [goal.id for goal in live])
            used = await feature_usage(
                self.session,
                project=project,
                customer=customer,
                memories=[*grouped.get(MemoryType.BEHAVIOR.value, []), *grouped.get(MemoryType.FACT.value, [])],
                recent_since=now - timedelta(days=rules.relied_on_days),
            )
            fed_hidden = await memories.events_feeding_hidden(
                project.id, [use.first_event_id for use in used], customer_ids=[customer.id]
            )

        problems = grouped.get(MemoryType.PROBLEM.value, [])
        names = sorted({str(name) for row in problems for name in (row.meta or {}).get("entity_names") or []})
        topic_types = await EntityRepository(self.session).types_of(project_id=project.id, names=names)
        return compute(
            PersonalizationInputs(
                now=now,
                facts=visible,
                rules=rules,
                guardrails=verdicts,
                problems=problems,
                goals=[goal for goal in live if goal.id not in hidden_goals],
                uses=[
                    Usage(use.kind, use.name, use.first_at, use.last_at, use.uses, use.recent, use.first_event_id)
                    for use in used
                    if use.first_event_id not in fed_hidden
                ],
                topic_types=topic_types,
                customer_name=customer.name or customer.external_id,
            )
        )

    async def refresh(
        self,
        *,
        project: Project,
        customer: Customer,
        facts: CustomerFacts | None = None,
        reason: str = "event",
        emit: bool = True,
    ) -> tuple[CustomerPersonalization, list[dict[str, Any]]]:
        """Compute and store; tell subscribers what changed. Returns the row and the changes."""
        document = await self.compute(project=project, customer=customer, facts=facts)
        version = fingerprint(document)
        row, previous, changed = await self.store.save(
            project_id=project.id,
            customer_id=customer.id,
            document=_jsonable(document),
            version=version,
            settings_hash=settings_hash(project),
            reason=reason,
        )
        moved = changes(previous, document) if changed and previous is not None else []
        if moved and emit:
            await WebhookDispatcher(self.session).emit(
                customer_personalization_changed(
                    project_id=project.id,
                    customer=customer,
                    changes=_jsonable(moved),
                    personalization=_jsonable(self.shape(row, customer)),
                )
            )
            logger.info(
                "personalization.changed",
                customer_id=customer.id,
                fields=[item["field"] for item in moved],
                reason=reason,
            )
        return row, moved

    # ------------------------------------------------------------------ read

    async def current(
        self, *, project: Project, customer: Customer, fresh: bool = False
    ) -> CustomerPersonalization:
        """The stored document — recomputed first when there is none, the settings changed
        since, it is older than a night, or the caller asked for ``fresh``."""
        row = await self.store.get(project_id=project.id, customer_id=customer.id)
        if (
            fresh
            or row is None
            or row.settings_hash != settings_hash(project)
            or stale(row.computed_at, utcnow())
        ):
            row, _ = await self.refresh(project=project, customer=customer, reason="read")
        return row

    async def for_reader(
        self,
        *,
        project: Project,
        customer: Customer,
        cleared: bool,
        details: bool = True,
        fresh: bool = False,
    ) -> dict[str, Any]:
        row = await self.current(project=project, customer=customer, fresh=fresh)
        hidden = await Reader(self.session, cleared=cleared).hidden(project.id, _cited(row.document))
        return self.shape(row, customer, details=details, hidden=hidden)

    async def batch(
        self, *, project: Project, customer_ids: Sequence[str], cleared: bool, details: bool = False
    ) -> list[dict[str, Any]]:
        """Many customers at once — for a server rendering a list. Unknown ids say so."""
        wanted = list(dict.fromkeys(str(ident).strip() for ident in customer_ids if str(ident).strip()))
        if not wanted:
            raise ValidationError("'customer_ids' must name at least one customer.")
        if len(wanted) > MAX_BATCH:
            raise ValidationError(f"At most {MAX_BATCH} customers per request.")
        repository = CustomerRepository(self.session)
        found: list[dict[str, Any]] = []
        for ident in wanted:
            customer = await repository.resolve(ident, project.id)
            if customer is None:
                found.append({"customer_id": ident, "personalization": None, "error": "not_found"})
                continue
            document = await self.for_reader(project=project, customer=customer, cleared=cleared, details=details)
            found.append({"customer_id": customer.external_id, "personalization": document, "error": None})
        return found

    def shape(
        self,
        row: CustomerPersonalization,
        customer: Customer,
        *,
        details: bool = False,
        hidden: frozenset[str] | set[str] = frozenset(),
    ) -> dict[str, Any]:
        """The stored document as one reader receives it: evidence they may not see removed —
        and a friction or goal known only from it — with the id, time and version."""
        document = json.loads(json.dumps(row.document or {}))
        if hidden:
            detail = document.get("details") or {}
            frictions = [
                item
                for item in detail.get("known_frictions") or []
                if any(ident not in hidden for ident in item.get("memory_ids") or [])
            ]
            for item in frictions:
                item["memory_ids"] = [ident for ident in item.get("memory_ids") or [] if ident not in hidden]
            kept = {item["key"] for item in frictions}
            document["known_frictions"] = [key for key in document.get("known_frictions") or [] if key in kept]
            if detail:
                detail["known_frictions"] = frictions
            goal = detail.get("current_goal")
            if goal and goal.get("id") in hidden:
                document["current_goal"] = None
                detail["current_goal"] = None
            document["evidence"] = [ident for ident in document.get("evidence") or [] if ident not in hidden]
        body = {
            "customer_id": customer.external_id,
            **document,
            "computed_at": row.computed_at,
            "changed_at": row.changed_at,
        }
        body["version"] = row.version if not hidden else fingerprint(document)
        if not details:
            body.pop("details", None)
        return body

    async def summary(self, *, project: Project) -> dict[str, Any]:
        return await self.store.summary(project_id=project.id)

    async def preview(self, *, project: Project, customer: Customer, raw_rules: dict[str, Any] | None) -> dict[str, Any]:
        """What proposed rules would produce for this customer — nothing stored, nothing sent."""
        try:
            rules = compile_rules(raw_rules) if raw_rules is not None else rules_for(project)
        except PersonalizationError as exc:
            raise ValidationError(str(exc)) from exc
        document = _jsonable(await self.compute(project=project, customer=customer, rules=rules))
        now = utcnow()
        return {
            "customer_id": customer.external_id,
            **document,
            "computed_at": now,
            "changed_at": now,
            "version": fingerprint(document),
        }


def _copy(facts: CustomerFacts) -> CustomerFacts:
    return replace(facts, values=dict(facts.values))


def _cited(document: dict[str, Any]) -> list[str]:
    detail = document.get("details") or {}
    ids = list(document.get("evidence") or [])
    for item in detail.get("known_frictions") or []:
        ids.extend(item.get("memory_ids") or [])
    goal = detail.get("current_goal") or {}
    if goal.get("id"):
        ids.append(goal["id"])
    return ids


def _jsonable(value: Any) -> Any:
    """Datetimes as ISO strings, for JSONB and webhook payloads."""
    return json.loads(json.dumps(value, default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item)))


__all__ = ["MAX_BATCH", "PersonalizationService", "flat", "rules_for", "settings_hash"]
