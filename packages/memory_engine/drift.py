"""Drift: evidence since a memory was stated that points the other way (§26 5.5).

A standing memory can be contradicted without anyone saying so. "We prefer email" — and
then twelve of their next fourteen contacts come through WhatsApp. "They are on Pro" — and
their last two invoices are for Enterprise. Four detectors, each reading only records that
are authoritative for the question:

* ``channel`` — a stated channel preference against the channels the customer has actually
  reached out through since (inbound contacts only: what *we* send says nothing about
  them);
* ``plan`` — the plan memory says against billing events since, which name the plan and
  create no memory of their own;
* ``usage`` — a feature memory says they use against feature events, when none has come in
  for a while and the customer stayed active otherwise (silence everywhere is a quiet
  customer, not a changed habit);
* ``quiet_problem`` — an open problem not reported for a while by a customer who stayed
  active: it may have been fixed without anyone saying so.

A finding is a *flag*: never a change. The memory stands until a person confirms the flag
(which writes the change through the normal paths) or dismisses it — after which only
evidence newer than the dismissal counts. Pure: the service hands over the rows.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.time import ensure_utc
from memory_engine.freshness import day, span
from nlp.entities import display_channel

KINDS = ("channel", "plan", "usage", "quiet_problem")
KIND_LABELS = {
    "channel": "contact channel",
    "plan": "plan",
    "usage": "feature use",
    "quiet_problem": "quiet problem",
}
MAX_EVIDENCE = 10


@dataclass(slots=True, frozen=True)
class DriftSettings:
    # Contacts on another channel, and their share of all contacts since, before a stated
    # channel preference is flagged.
    min_contacts: int = 5
    min_share: float = 0.6
    # Consecutive billing events on another plan before the plan memory is flagged.
    min_billing_events: int = 2
    # How long an open problem, or a feature they said they use, can go unmentioned while
    # the customer stays active.
    quiet_problem_days: int = 30
    quiet_usage_days: int = 60
    # Events since, to count as "stayed active".
    min_activity: int = 5

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_contacts": self.min_contacts,
            "min_share": self.min_share,
            "min_billing_events": self.min_billing_events,
            "quiet_problem_days": self.quiet_problem_days,
            "quiet_usage_days": self.quiet_usage_days,
            "min_activity": self.min_activity,
        }


def settings_for(project_settings: Mapping[str, Any] | None) -> DriftSettings:
    """The project's drift thresholds over the defaults."""
    stored = project_settings or {}
    base = DriftSettings()
    quiet = stored.get("drift_quiet_days") or {}
    quiet = quiet if isinstance(quiet, Mapping) else {}

    def number(value: Any, default: float) -> float:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

    return DriftSettings(
        min_contacts=int(number(stored.get("drift_min_contacts"), base.min_contacts)),
        min_share=number(stored.get("drift_min_share"), base.min_share),
        min_billing_events=int(number(stored.get("drift_min_billing_events"), base.min_billing_events)),
        quiet_problem_days=int(number(quiet.get("problem"), base.quiet_problem_days)),
        quiet_usage_days=int(number(quiet.get("usage"), base.quiet_usage_days)),
        min_activity=int(number(stored.get("drift_min_activity"), base.min_activity)),
    )


@dataclass(slots=True, frozen=True)
class Contact:
    """The customer reaching out: through which channel, when, and the record that says so."""

    channel: str
    at: datetime
    ref: str


@dataclass(slots=True, frozen=True)
class Billing:
    plan: str
    at: datetime
    ref: str


@dataclass(slots=True)
class Finding:
    kind: str
    memory_id: str
    stated: str
    observed: str | None
    summary: str
    since: datetime
    counts: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "memory_id": self.memory_id,
            "stated": self.stated,
            "observed": self.observed,
            "summary": self.summary,
            "since": self.since,
            "counts": dict(self.counts),
            "evidence": list(self.evidence),
        }


def _after(moment: datetime, since: datetime) -> bool:
    return ensure_utc(moment) > ensure_utc(since)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def channel_drift(
    *,
    memory_id: str,
    stated: str,
    since: datetime,
    contacts: Sequence[Contact],
    settings: DriftSettings,
    stated_at: datetime | None = None,
) -> Finding | None:
    """The stated channel against the channels they reached out through since. ``since`` is
    where counting starts (a dismissal restarts it); ``stated_at`` is when they said it."""
    after = [contact for contact in contacts if _after(contact.at, since)]
    if len(after) < settings.min_contacts:
        return None
    tally = Counter(contact.channel for contact in after)
    total = sum(tally.values())
    # Most used; ties broken by name so the same rows always give the same answer.
    top, top_count = max(tally.items(), key=lambda item: (item[1], str(item[0]).lower()))
    stated_key = str(stated).lower()
    if str(top).lower() == stated_key:
        return None
    if top_count < settings.min_contacts or top_count / total < settings.min_share:
        return None
    on_stated = sum(count for channel, count in tally.items() if str(channel).lower() == stated_key)
    shown_stated = display_channel(stated) or stated
    summary = (
        f"They said they prefer {shown_stated} on {day(stated_at or since)}; since "
        + ("then" if stated_at is None or ensure_utc(stated_at) >= ensure_utc(since) else f"{day(since)}")
        + f" {top_count} of their {_plural(total, 'contact')} came through {top}"
        + (f" and {on_stated} through {shown_stated}" if on_stated else "")
        + "."
    )
    evidence = [
        contact.ref
        for contact in sorted(after, key=lambda item: ensure_utc(item.at), reverse=True)
        if contact.channel == top
    ][:MAX_EVIDENCE]
    return Finding(
        "channel",
        memory_id,
        shown_stated,
        str(top),
        summary,
        ensure_utc(since),
        counts={"by_channel": dict(tally), "total": total, "observed": top_count, "stated": on_stated},
        evidence=evidence,
    )


def plan_drift(
    *,
    memory_id: str,
    stated: str,
    since: datetime,
    billing: Sequence[Billing],
    settings: DriftSettings,
    stated_at: datetime | None = None,
) -> Finding | None:
    """The plan memory says against the plan the latest billing events name."""
    after = sorted((item for item in billing if _after(item.at, since)), key=lambda item: ensure_utc(item.at))
    if not after:
        return None
    latest = after[-1].plan
    if latest.lower() == str(stated).lower():
        return None
    run: list[Billing] = []
    for item in reversed(after):  # the unbroken run of the latest plan, newest first
        if item.plan.lower() != latest.lower():
            break
        run.append(item)
    if len(run) < settings.min_billing_events:
        return None
    summary = (
        f"Memory says the {str(stated).title()} plan (as of {day(stated_at or since)}); their last "
        f"{_plural(len(run), 'billing event')} were for the {latest.title()} plan, most recently on "
        f"{day(run[0].at)}."
    )
    return Finding(
        "plan",
        memory_id,
        str(stated).lower(),
        latest.lower(),
        summary,
        ensure_utc(since),
        counts={"events": len(run), "since_statement": len(after)},
        evidence=[item.ref for item in run][:MAX_EVIDENCE],
    )


def usage_drift(
    *,
    memory_id: str,
    feature: str,
    last_used_at: datetime | None,
    activity_since: int,
    settings: DriftSettings,
    now: datetime,
    counting_from: datetime | None = None,
) -> Finding | None:
    """A feature they said they use, unused for a while by a customer who stayed active.

    ``counting_from`` restarts the clock after a dismissal; the sentence still says when the
    feature was last used."""
    if last_used_at is None:
        return None
    quiet = int((ensure_utc(now) - ensure_utc(last_used_at)).total_seconds() // 86400)
    counted = int((ensure_utc(now) - ensure_utc(counting_from or last_used_at)).total_seconds() // 86400)
    if min(quiet, counted) < settings.quiet_usage_days or activity_since < settings.min_activity:
        return None
    summary = (
        f"No {feature} use in {span(quiet)} (last on {day(last_used_at)}), while they stayed "
        f"active: {_plural(activity_since, 'event')} since."
    )
    return Finding(
        "usage",
        memory_id,
        feature,
        None,
        summary,
        ensure_utc(last_used_at),
        counts={"quiet_days": quiet, "activity": activity_since},
    )


def quiet_problem(
    *,
    memory_id: str,
    content: str,
    evidence_at: datetime,
    activity_since: int,
    settings: DriftSettings,
    now: datetime,
    counting_from: datetime | None = None,
) -> Finding | None:
    """An open problem not reported for a while by a customer who stayed active."""
    quiet = int((ensure_utc(now) - ensure_utc(evidence_at)).total_seconds() // 86400)
    counted = int((ensure_utc(now) - ensure_utc(counting_from or evidence_at)).total_seconds() // 86400)
    if min(quiet, counted) < settings.quiet_problem_days or activity_since < settings.min_activity:
        return None
    summary = (
        f"Not reported in {span(quiet)} while they stayed active "
        f"({_plural(activity_since, 'event')} since) — it may have been fixed."
    )
    words = " ".join(content.split())
    return Finding(
        "quiet_problem",
        memory_id,
        words if len(words) <= 200 else words[:199].rstrip() + "…",
        None,
        summary,
        ensure_utc(evidence_at),
        counts={"quiet_days": quiet, "activity": activity_since},
    )
