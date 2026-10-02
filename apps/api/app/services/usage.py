"""What a customer uses: features and integrations, from the events themselves (§26 6.6).

Shared by the journey ("Started using Shopify") and personalization ("features used",
"relied on"), so both name the same things the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import MemoryType
from common.time import ensure_utc
from database.models import Customer, Project
from database.repositories import EventRepository
from database.repositories.events import FeatureUse
from nlp.templates import display_name


def _key(name: Any) -> str:
    return " ".join(str(name or "").lower().replace("_", " ").replace("-", " ").split())


async def feature_usage(
    session: AsyncSession,
    *,
    project: Project,
    customer: Customer,
    memories: Iterable[Any] = (),
    recent_since: datetime | None = None,
) -> list[FeatureUse]:
    """Every feature and integration the customer used, oldest first — from their events,
    and, for what no event names, from what they said they use (a behaviour or fact memory
    whose template recorded the feature or integration)."""
    found: dict[str, FeatureUse] = {}
    for use in await EventRepository(session).first_uses(
        project_id=project.id, customer_id=customer.id, recent_since=recent_since
    ):
        found.setdefault(use.key, use._replace(name=display_name(use.name)))
    for memory in memories:
        if str(memory.type) not in (MemoryType.BEHAVIOR.value, MemoryType.FACT.value):
            continue
        meta = memory.meta or {}
        name = meta.get("integration") or meta.get("feature")
        if not name or not memory.source_event_ids or _key(name) in found:
            continue
        last = ensure_utc(memory.last_seen_at)
        recent = int(memory.evidence_count or 1) if recent_since is not None and last >= ensure_utc(recent_since) else 0
        found[_key(name)] = FeatureUse(
            "integration" if meta.get("integration") else "feature",
            _key(name),
            str(name),
            str(memory.source_event_ids[0]),
            memory.first_seen_at,
            memory.last_seen_at,
            int(memory.evidence_count or 1),
            recent,
            "",
        )
    return sorted(found.values(), key=lambda use: ensure_utc(use.first_at))


__all__ = ["feature_usage"]
