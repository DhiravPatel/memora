"""When a customer's state has changed enough to be worth remembering.

A snapshot per event would record noise: health drifts a fraction of a point as memories
age, and a customer sending fifty page views a day would write fifty identical rows. A
snapshot only when *something a person would care about* moved keeps the history readable
and the table proportional to what happened rather than to traffic.

"Something a person would care about" is :data:`MATERIAL`: bands rather than raw scores,
counts rather than contents, and continuous numbers bucketed coarsely enough that decay
alone cannot cross a bucket in a day.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from memory_engine.facts import CustomerFacts

# fact -> how to reduce it before comparing. ``None`` means compare as-is.
MATERIAL: dict[str, Any] = {
    "health.band": None,
    "health.score": lambda value: None if value is None else int(float(value) // 5),
    "state.current": None,
    "subscription.plan": None,
    "subscription.direction": None,
    "signals.trajectory": None,
    "signals.churn_risk": lambda value: None if value is None else round(float(value), 1),
    "signals.expansion_score": lambda value: None if value is None else round(float(value), 1),
    "problems.open_count": None,
    "goals.open_count": None,
    "goals.progressing_count": None,
    "goals.stalled_count": None,
    "goals.achieved_count": None,
    "intents.kinds": lambda value: sorted(value or []),
    "preferences.channel": None,
    "activity.trend": None,
}

# What a change record reports — the material facts, shown as they were rather than as
# their buckets, plus the problem entities, whose churn is the most-asked "what changed?".
TRACKED = (*MATERIAL, "problems.entities", "signals.active")


def fingerprint(facts: CustomerFacts) -> str:
    reduced = {}
    for name, reduce in MATERIAL.items():
        value = facts.get(name)
        reduced[name] = reduce(value) if reduce else value
    encoded = json.dumps(reduced, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def changes(previous: dict[str, Any] | None, current: CustomerFacts) -> list[dict[str, Any]]:
    """What moved, fact by fact. The first snapshot has no "before", and says so."""
    before_values = (previous or {}).get("values", {}) if previous else {}
    found: list[dict[str, Any]] = []
    for name in TRACKED:
        after = current.get(name)
        before = before_values.get(name) if previous else None
        if previous is not None and _same(before, after):
            continue
        if previous is None and after in (None, [], 0):
            continue
        entry: dict[str, Any] = {"fact": name, "before": before, "after": after}
        if isinstance(after, list) or isinstance(before, list):
            old, new = set(before or []), set(after or [])
            entry["added"] = sorted(new - old)
            entry["removed"] = sorted(old - new)
        found.append(entry)
    return found


def indexed(facts: CustomerFacts) -> dict[str, Any]:
    """The copies stored as columns, so lists and charts need not open the JSON."""
    return {
        "health_score": facts.get("health.score"),
        "health_band": facts.get("health.band"),
        "state": facts.get("state.current"),
        "plan": facts.get("subscription.plan"),
        "trajectory": facts.get("signals.trajectory"),
        "open_problems": facts.get("problems.open_count") or 0,
        "churn_risk": facts.get("signals.churn_risk"),
        "expansion_score": facts.get("signals.expansion_score"),
    }


def _same(before: Any, after: Any) -> bool:
    if isinstance(before, list) or isinstance(after, list):
        return sorted(map(str, before or [])) == sorted(map(str, after or []))
    return before == after
