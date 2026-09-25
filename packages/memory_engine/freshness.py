"""Freshness: a memory can be valid and still out of date (§26 5.5).

"The customer prefers email" said eight months ago is not wrong, but it is not what it was
the week they said it. Every memory gets a **freshness state** and an **effective
confidence** that decays without supporting evidence:

* ``active`` — recently evidenced;
* ``aging`` — past half its type's window with nothing new;
* ``stale`` — past its window with nothing new;
* ``outdated`` — evidence since points the other way (an open drift flag, :mod:`memory_engine.drift`);
* ``conflicted`` — contradicted after its last support by a statement judged weaker, and
  not confirmed since;
* ``expired`` / ``superseded`` — no longer standing at all.

Windows are per memory type, because types age differently: a problem nobody mentions for
a month has probably been fixed or given up on, a job title holds for a year. *Evidence* is
the later of the last time the customer said it and the last time a person confirmed it.

Pure: the caller hands over the memory, the moment, the window, the latest contradiction
and any open drift flags.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.time import ensure_utc

STATES = ("active", "aging", "stale", "outdated", "conflicted", "expired", "superseded")
# States a person should look at.
ATTENTION = ("stale", "outdated", "conflicted")

# Days without new evidence after which a memory of each type is stale; it is aging from
# half that. Chosen for how long each kind of statement typically stays true.
DEFAULT_WINDOWS: dict[str, int] = {
    "problem": 30,
    "intent": 45,
    "summary": 30,
    "behavior": 60,
    "goal": 90,
    "feedback": 90,
    "preference": 180,
    "subscription": 365,
    "fact": 365,
    "relationship": 365,
}
FALLBACK_WINDOW = 180
MIN_WINDOW, MAX_WINDOW = 1, 3650

# Effective confidence halves every window without evidence, but never falls below this share
# of the stored confidence: time alone does not disprove a statement.
FLOOR = 0.15
# A contradiction judged weaker still costs a quarter of the trust; evidence pointing the
# other way (an open drift flag) costs more.
CONFLICT_FACTOR = 0.75
OUTDATED_FACTOR = 0.6

# How a sentence names each type in the plural: "problems go stale after 4 weeks".
_PLURALS = {
    "summary": "summaries",
    "feedback": "feedback memories",
    "behavior": "usage memories",
    "subscription": "plan statements",
}


def windows(settings: Mapping[str, Any] | None) -> dict[str, int]:
    """The project's windows: the defaults, with any the project set (``freshness_days``)."""
    merged = dict(DEFAULT_WINDOWS)
    custom = (settings or {}).get("freshness_days") or {}
    if isinstance(custom, Mapping):
        for kind, days in custom.items():
            if isinstance(days, (int, float)) and not isinstance(days, bool):
                merged[str(kind)] = max(MIN_WINDOW, min(MAX_WINDOW, int(days)))
    return merged


def evidence_at(memory: Any) -> datetime:
    """The later of the last time the customer said it and the last time a person confirmed it."""
    seen = ensure_utc(memory.last_seen_at)
    confirmed = _moment((getattr(memory, "meta", None) or {}).get("confirmed_at"))
    return max(seen, confirmed) if confirmed is not None else seen


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str) and value:
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


@dataclass(slots=True, frozen=True)
class Freshness:
    state: str
    effective_confidence: float
    evidence_at: datetime
    days_since_evidence: int
    window_days: int
    reasons: tuple[str, ...] = ()
    contradicted_at: datetime | None = None
    # Open drift flags on the memory: id, kind, summary.
    drift: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def needs_attention(self) -> bool:
        return self.state in ATTENTION

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "effective_confidence": round(self.effective_confidence, 3),
            "evidence_at": self.evidence_at,
            "days_since_evidence": self.days_since_evidence,
            "window_days": self.window_days,
            "reasons": list(self.reasons),
            "contradicted_at": self.contradicted_at,
            "drift": list(self.drift),
        }


def _plural(kind: str) -> str:
    return _PLURALS.get(kind, f"{kind}s")


def span(days: int) -> str:
    """"3 days", "5 weeks", "7 months", "over a year" — for a sentence."""
    if days <= 1:
        return "a day" if days == 1 else "less than a day"
    if days < 14:
        return f"{days} days"
    if days < 60:
        return f"{days // 7} weeks"
    if days < 365:
        return f"{days // 30} months"
    years = days // 365
    return "over a year" if years == 1 else f"over {years} years"


def day(moment: datetime) -> str:
    """"12 Sep 2026", portably (no platform-specific strftime flags)."""
    moment = ensure_utc(moment)
    return f"{moment.day} {moment:%b %Y}"


def assess(
    memory: Any,
    *,
    now: datetime,
    window_days: int,
    contradicted_at: datetime | None = None,
    drift: Sequence[Mapping[str, Any]] = (),
) -> Freshness:
    """One memory's freshness now."""
    kind = str(memory.type)
    status = str(memory.status)
    stated = float(memory.confidence or 0.0)
    evidenced = evidence_at(memory)
    days = max(0, int((ensure_utc(now) - evidenced).total_seconds() // 86400))
    window = max(MIN_WINDOW, int(window_days))
    decay = max(FLOOR, 0.5 ** (days / window))
    effective = stated * decay
    open_drift = tuple({"id": flag.get("id"), "kind": flag.get("kind"), "summary": flag.get("summary")} for flag in drift)
    contradiction = ensure_utc(contradicted_at) if contradicted_at is not None else None
    base: dict[str, Any] = {
        "evidence_at": evidenced,
        "days_since_evidence": days,
        "window_days": window,
        "drift": open_drift,
    }

    if status == "superseded":
        return Freshness("superseded", 0.0, reasons=("Replaced by a newer memory.",), **base)
    expires = getattr(memory, "expires_at", None)
    if status == "expired" or (expires is not None and ensure_utc(expires) <= ensure_utc(now)):
        when = f" on {day(expires)}" if expires is not None else ""
        return Freshness("expired", 0.0, reasons=(f"Expired{when}.",), **base)
    if contradiction is not None and contradiction > evidenced:
        return Freshness(
            "conflicted",
            effective * CONFLICT_FACTOR,
            reasons=(f"Contradicted on {day(contradiction)} by a statement judged weaker, and not confirmed since.",),
            contradicted_at=contradiction,
            **base,
        )
    if open_drift:
        return Freshness(
            "outdated",
            effective * OUTDATED_FACTOR,
            reasons=tuple(str(flag["summary"]) for flag in open_drift if flag.get("summary")),
            **base,
        )
    if days >= window:
        return Freshness(
            "stale",
            effective,
            reasons=(f"No new evidence in {span(days)}; {_plural(kind)} go stale after {span(window)}.",),
            **base,
        )
    if days * 2 >= window:
        return Freshness(
            "aging",
            effective,
            reasons=(f"No new evidence in {span(days)}; {_plural(kind)} go stale after {span(window)}.",),
            **base,
        )
    return Freshness("active", effective, **base)


def assess_many(
    memories: Iterable[Any],
    *,
    now: datetime,
    windows_by_type: Mapping[str, int],
    contradictions: Mapping[str, datetime] | None = None,
    drift_by_memory: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Freshness]:
    contradictions = contradictions or {}
    drift_by_memory = drift_by_memory or {}
    return {
        memory.id: assess(
            memory,
            now=now,
            window_days=windows_by_type.get(str(memory.type), FALLBACK_WINDOW),
            contradicted_at=contradictions.get(memory.id),
            drift=drift_by_memory.get(memory.id, ()),
        )
        for memory in memories
    }


def counts(assessed: Iterable[Freshness]) -> dict[str, int]:
    found = dict.fromkeys(STATES, 0)
    for item in assessed:
        found[item.state] = found.get(item.state, 0) + 1
    return found


def note(freshness: Freshness | None) -> str | None:
    """The few words an agent's context carries about a memory that is not fresh."""
    if freshness is None or freshness.state in ("active", "aging"):
        return None
    if freshness.state == "stale":
        return f"stale: last evidence {span(freshness.days_since_evidence)} ago"
    if freshness.state == "conflicted" and freshness.contradicted_at is not None:
        return f"contradicted on {day(freshness.contradicted_at)}"
    if freshness.state == "outdated":
        return "possibly outdated: " + "; ".join(freshness.reasons)
    return freshness.state


async def gather(
    memories: Sequence[Any],
    *,
    project_id: str,
    project_settings: Mapping[str, Any] | None,
    memory_repository: Any,
    drift_repository: Any,
    now: datetime,
) -> dict[str, Freshness]:
    """Freshness for rows already loaded, with the two lookups it needs — the latest
    contradiction of each, and its open drift flags — in one query each. The repositories
    are passed in, so this module stays free of the database."""
    if not memories:
        return {}
    ids = [memory.id for memory in memories]
    contradictions = await memory_repository.latest_contradictions(ids)
    flags = await drift_repository.open_for_memories(project_id, ids)
    return assess_many(
        memories,
        now=now,
        windows_by_type=windows(project_settings),
        contradictions=contradictions,
        drift_by_memory={
            memory_id: [{"id": flag.id, "kind": flag.kind, "summary": flag.summary} for flag in rows]
            for memory_id, rows in flags.items()
        },
    )
