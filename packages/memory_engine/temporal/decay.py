"""Temporal behaviour: freshness, decay and expiry.

Some memories are observations about a moment ("currently evaluating Shopify") and stop
being useful; durable facts ("uses Shopify") must never silently disappear.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from common.enums import MemoryType
from common.time import days_between, utcnow

# Memory types that describe a durable property of the customer never expire.
DURABLE_TYPES = {
    MemoryType.FACT,
    MemoryType.PREFERENCE,
    MemoryType.RELATIONSHIP,
    MemoryType.SUBSCRIPTION,
    MemoryType.FEEDBACK,
}

# Multipliers applied to the project's default decay window.
DECAY_MULTIPLIERS: dict[MemoryType, float] = {
    MemoryType.INTENT: 0.5,
    MemoryType.GOAL: 2.0,
    MemoryType.PROBLEM: 1.5,
    MemoryType.BEHAVIOR: 1.0,
    MemoryType.SUMMARY: 1.0,
}

# Half-life in days used when scoring how "fresh" a memory is at retrieval time.
RECENCY_HALF_LIFE: dict[MemoryType, float] = {
    MemoryType.INTENT: 7.0,
    MemoryType.PROBLEM: 21.0,
    MemoryType.BEHAVIOR: 30.0,
    MemoryType.GOAL: 60.0,
    MemoryType.SUMMARY: 45.0,
    MemoryType.FACT: 180.0,
    MemoryType.PREFERENCE: 180.0,
    MemoryType.SUBSCRIPTION: 120.0,
    MemoryType.RELATIONSHIP: 180.0,
    MemoryType.FEEDBACK: 90.0,
}

DEFAULT_HALF_LIFE = 60.0


def expiry_for(
    memory_type: MemoryType | str, occurred_at: datetime, decay_days: int
) -> datetime | None:
    """When a memory of this type should stop being served, if ever."""
    if decay_days <= 0:
        return None
    try:
        parsed = MemoryType(str(memory_type))
    except ValueError:
        parsed = MemoryType.FACT
    if parsed in DURABLE_TYPES:
        return None
    multiplier = DECAY_MULTIPLIERS.get(parsed, 1.0)
    return occurred_at + timedelta(days=decay_days * multiplier)


def recency_score(
    last_seen_at: datetime, memory_type: MemoryType | str | None = None, now: datetime | None = None
) -> float:
    """1.0 for something seen just now, decaying by type-specific half-life."""
    half_life = DEFAULT_HALF_LIFE
    if memory_type is not None:
        try:
            half_life = RECENCY_HALF_LIFE.get(MemoryType(str(memory_type)), DEFAULT_HALF_LIFE)
        except ValueError:
            half_life = DEFAULT_HALF_LIFE
    age_days = max(0.0, days_between(last_seen_at, now or utcnow()))
    return round(0.5 ** (age_days / half_life), 6)


def is_stale(
    last_seen_at: datetime, memory_type: MemoryType | str, *, threshold: float = 0.1
) -> bool:
    return recency_score(last_seen_at, memory_type) < threshold
