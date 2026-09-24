"""What changed about a customer between two moments (§26 4.1).

"I don't want their whole history — tell me what changed since I last spoke to them." The
answer is assembled from records that already exist, each read for the one thing it is
authoritative on:

* **memories first seen in the window** — problems opened and resolved, plans and
  preferences that changed (a statement that superseded an older one gives *before* and
  *after*), intents expressed, strong feedback, facts and relationships learned;
* **memory versions written in the window** — a problem reported again, a correction;
* **goal evidence** — goals set, progressing, achieved, stalled, abandoned;
* **lifecycle stays entered in the window** — on every track, with reasons in words;
* **snapshots at each end** — health bands crossed, risk and trajectory, what the customer
  looked like *then* and *now*;
* **signals** firing at each end, and **event counts** against the window before.

Everything here is pure. The service gathers rows; :func:`detect` turns them into typed
changes, each with before, after, when and evidence; :func:`shape` applies one reader's
view. A change's title only ever quotes its own *subject* — the record that must be
visible for the change to be shown at all — so shaping can withhold a hidden *before*
without rewriting a sentence around it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from common.time import ensure_utc
from memory_engine.facts import (
    DAYS_SUFFIX,
    LIFECYCLE_PREFIX,
    is_negative,
    plan_of,
    plans_named,
    polarity,
)
from memory_engine.policy import WITHHELD
from memory_engine.reasons import reasons as reasons_in_words
from memory_engine.reasons import sentence
from memory_engine.snapshots import TRACKED
from nlp.intents import intent_kinds

# How much a change matters, for ordering by importance and for the summary.
_IMPORTANCE = {
    ("subscription", "cancelled"): 1.0,
    ("subscription", "changed"): 0.95,
    ("lifecycle", "moved"): 0.9,
    ("health", "crossed"): 0.9,
    ("problem", "opened"): 0.85,
    ("problem", "resolved"): 0.85,
    ("intent", "expressed"): 0.8,
    ("preference", "changed"): 0.75,
    ("goal", "achieved"): 0.75,
    ("activity", "quiet"): 0.7,
    ("subscription", "renewed"): 0.7,
    ("goal", "abandoned"): 0.7,
    ("goal", "stalled"): 0.7,
    ("feedback", "rose"): 0.7,
    ("feedback", "negative"): 0.65,
    ("activity", "fell"): 0.65,
    ("problem", "recurring"): 0.6,
    ("goal", "opened"): 0.6,
    ("health", "fell"): 0.6,
    ("risk", "rose"): 0.6,
    ("relationship", "added"): 0.6,
    ("relationship", "changed"): 0.6,
    ("signal", "started"): 0.55,
    ("trajectory", "changed"): 0.55,
    ("health", "rose"): 0.55,
    ("memory", "corrected"): 0.5,
    ("goal", "progressed"): 0.5,
    ("goal", "changed"): 0.5,
    ("fact", "changed"): 0.5,
    ("risk", "fell"): 0.5,
    ("activity", "rose"): 0.45,
    ("preference", "added"): 0.45,
    ("feedback", "positive"): 0.45,
    ("feedback", "fell"): 0.4,
    ("signal", "stopped"): 0.4,
    ("fact", "added"): 0.35,
}

TYPES = (
    "subscription", "lifecycle", "health", "risk", "trajectory", "problem", "intent",
    "preference", "goal", "feedback", "relationship", "fact", "memory", "signal", "activity",
)

# Thresholds for the end-to-end comparisons.
HEALTH_POINTS = 10.0
RISK_STEP = 0.15
ACTIVITY_STEP = 25.0
STRONG_FEEDBACK = 0.5
FEEDBACK_QUOTES = 5


@dataclass(slots=True)
class Change:
    type: str
    kind: str
    title: str
    detected_at: datetime
    before: str | None = None
    after: str | None = None
    evidence: list[str] = field(default_factory=list)
    source: str = "memory"  # memory | version | goal | lifecycle | snapshot | signal | activity
    track: str | None = None
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    # The record that must be visible for the change to be shown at all, and the record
    # ``before`` quotes. Neither is shown; both are for :func:`shape`.
    subject: str | None = None
    before_id: str | None = None

    @property
    def importance(self) -> float:
        return _IMPORTANCE.get((self.type, self.kind), 0.4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "kind": self.kind,
            "title": self.title,
            "before": self.before,
            "after": self.after,
            "detected_at": self.detected_at,
            "evidence": self.evidence[:10],
            "source": self.source,
            "track": self.track,
            "reasons": self.reasons,
            "detail": self.detail,
            "importance": round(self.importance, 2),
        }


@dataclass(slots=True)
class ChangeInputs:
    since: datetime
    until: datetime
    # Memories first seen in the window, any status but deleted.
    memories: Sequence[Any] = ()
    # Memories the window's memories refer to — what they superseded or corrected, and
    # what superseded them — by id.
    related: dict[str, Any] = field(default_factory=dict)
    # The customer's newest subscription statement from before the window.
    previous_subscription: Any | None = None
    # Feedback first seen in the equal window before this one, for the trend.
    prior_feedback: Sequence[Any] = ()
    # (version, memory) for versions written in the window, creations excluded.
    versions: Sequence[tuple[Any, Any]] = ()
    goals: Sequence[Any] = ()
    # Lifecycle stays begun in the window, every track, each with its evaluation as the
    # reader may see it.
    states: Sequence[tuple[Any, dict[str, Any]]] = ()
    track_labels: dict[str, str] = field(default_factory=dict)
    # Fact values at each end as the reader may see them. ``then`` is None when nothing
    # was recorded before the window began.
    then: dict[str, Any] | None = None
    now: dict[str, Any] = field(default_factory=dict)
    # When each fact last moved inside the window, from the snapshots' own change lists.
    moments: dict[str, datetime] = field(default_factory=dict)
    signal_labels: dict[str, str] = field(default_factory=dict)
    # Events in the window, and in the equal window before it.
    events_now: int = 0
    events_before: int = 0
    # When the customer's history begins — their first event or their record, whichever is
    # earlier. Activity is only compared with a window they existed for.
    customer_since: datetime | None = None


def detect(inputs: ChangeInputs) -> list[Change]:
    """Every change in the window, newest first, before any reader's view is applied."""
    found: list[Change] = []
    found.extend(_from_memories(inputs))
    found.extend(_from_feedback(inputs))
    found.extend(_from_versions(inputs))
    found.extend(_from_goals(inputs))
    found.extend(_from_states(inputs))
    found.extend(_from_snapshots(inputs, already={(change.type, change.kind) for change in found}))
    found.extend(_from_signals(inputs))
    found.extend(_from_activity(inputs))
    found.sort(key=lambda change: change.detected_at, reverse=True)
    return found


# Details derived from the *before* record, withheld with it.
_BEFORE_DETAILS = frozenset({"previous_plan"})


def shape(changes: Sequence[Change], hidden: Iterable[str]) -> tuple[list[Change], int]:
    """The changes one reader may see, and how many were withheld from them.

    A change whose subject is hidden is dropped and counted — "2 changes you may not see"
    is honest where silence is not. A hidden *before* is masked; evidence loses the ids
    the reader may not see.
    """
    hidden = frozenset(hidden)
    if not hidden:
        return list(changes), 0
    shown: list[Change] = []
    withheld = 0
    for change in changes:
        if change.subject is not None and change.subject in hidden:
            withheld += 1
            continue
        if change.before_id is not None and change.before_id in hidden:
            detail = {key: value for key, value in change.detail.items() if key not in _BEFORE_DETAILS}
            change = replace(change, before=WITHHELD, detail=detail)
        if any(ident in hidden for ident in change.evidence):
            change = replace(change, evidence=[ident for ident in change.evidence if ident not in hidden])
        shown.append(change)
    return shown, withheld


def cited_ids(changes: Iterable[Change]) -> set[str]:
    """Every record the changes name — what a reader's hidden set is computed over."""
    found: set[str] = set()
    for change in changes:
        found.update(change.evidence)
        found.update(ident for ident in (change.subject, change.before_id) if ident)
    return found


# ------------------------------------------------------------------ memories


def _meta(memory: Any) -> dict[str, Any]:
    meta = getattr(memory, "meta", None)
    return meta if isinstance(meta, dict) else {}


def _words(value: Any) -> str:
    return str(value).replace("_", " ")


def _plan_name(plan: str | None) -> str | None:
    return plan.title() if plan else None


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _stands_in_for_another(memory: Any, related: dict[str, Any], merged: set[str]) -> bool:
    """Whether a superseded memory is not a change of its own: a person rejected it, or
    corrected it — and then the correction speaks for it — or the nightly sweep merged it
    into a memory that says the same thing."""
    if str(memory.status) != "superseded":
        return False
    if memory.id in merged:
        return True
    if _meta(memory).get("human_rejected"):
        return True
    replacement = related.get(str(getattr(memory, "superseded_by", None) or ""))
    return replacement is not None and _meta(replacement).get("corrects") == memory.id


def _from_memories(inputs: ChangeInputs) -> Iterable[Change]:
    previous = inputs.previous_subscription
    merged = {memory.id for version, memory in inputs.versions if version.reason == "offline_consolidation"}
    for memory in sorted(inputs.memories, key=lambda item: ensure_utc(item.first_seen_at)):
        if _stands_in_for_another(memory, inputs.related, merged):
            continue
        kind = str(memory.type)
        meta = _meta(memory)
        when = ensure_utc(memory.first_seen_at)
        replaced = inputs.related.get(str(meta.get("supersedes") or ""))
        base: dict[str, Any] = {
            "detected_at": when,
            "after": memory.content,
            "subject": memory.id,
            "evidence": [memory.id, *([replaced.id] if replaced is not None else [])],
        }
        if replaced is not None:
            base.update(before=replaced.content, before_id=replaced.id)

        # "It works now" is typed a fact by the extractor; it resolves a problem when it
        # superseded one (the consolidator's problem_resolved rule).
        if meta.get("resolved") and (kind == "problem" or (replaced is not None and str(replaced.type) == "problem")):
            yield Change("problem", "resolved", f"Resolved: {memory.content}", **base)
        elif kind == "problem":
            yield Change("problem", "opened", memory.content, **base)
        elif kind == "subscription":
            yield _subscription_change(memory, replaced or previous, when)
            previous = memory
        elif kind == "preference":
            if replaced is not None:
                yield Change("preference", "changed", f"Preference changed: {memory.content}", **base)
            else:
                yield Change("preference", "added", memory.content, **base)
        elif kind == "intent":
            kinds = intent_kinds(memory.content)
            title = f"{_words(kinds[0]).capitalize()} intent: {memory.content}" if kinds else memory.content
            yield Change("intent", "expressed", title, detail={"kinds": kinds}, **base)
        elif kind == "relationship":
            yield Change("relationship", "changed" if replaced is not None else "added", memory.content, **base)
        elif kind in ("fact", "behavior"):
            yield Change("fact", "changed" if replaced is not None else "added", memory.content, **base)
        # Goals are reported from the goal tracker, feedback by _from_feedback, and
        # summaries restate everything else: none of them is a change of its own.


def _subscription_change(memory: Any, prior: Any | None, when: datetime) -> Change:
    plan, direction = plan_of(memory)
    previous_plan = plan_of(prior)[0] if prior is not None else None
    name = _plan_name(plan)
    # Only a plan the subject itself names goes in the title ("upgraded from Starter to
    # Pro"). One read from the earlier statement is `before`, which a reader may not see.
    named = plans_named(memory.content)
    own_previous = named[0] if len(named) >= 2 and named[0] != plan else None
    if direction == "cancelled":
        kind, title = "cancelled", f"Cancelled the {name} plan" if name else "Cancelled the subscription"
    elif direction in ("upgraded", "downgraded") and name:
        via = f" from {_plan_name(own_previous)}" if own_previous else ""
        kind, title = "changed", f"{direction.capitalize()}{via} to {name}"
    elif direction == "renewed":
        kind, title = "renewed", f"Renewed the {name} plan" if name else "Renewed the subscription"
    elif direction == "started" and name:
        kind, title = "changed", f"Started on the {name} plan"
    elif name:
        kind, title = "changed", f"Now on the {name} plan"
    else:
        kind, title = "changed", memory.content
    detail = {
        key: value
        for key, value in (("plan", plan), ("previous_plan", previous_plan), ("direction", direction))
        if value
    }
    return Change(
        "subscription",
        kind,
        title,
        when,
        before=prior.content if prior is not None else None,
        after=memory.content,
        evidence=[memory.id, *([prior.id] if prior is not None else [])],
        detail=detail,
        subject=memory.id,
        before_id=prior.id if prior is not None else None,
    )


def _from_feedback(inputs: ChangeInputs) -> Iterable[Change]:
    feedback = [memory for memory in inputs.memories if str(memory.type) == "feedback"]
    # Strong feedback is quoted: "terrible support experience" is a change a person wants
    # to see in the customer's words. Mild feedback only counts toward the trend.
    strong = sorted(
        (memory for memory in feedback if abs(polarity(memory)) >= STRONG_FEEDBACK),
        key=lambda memory: ensure_utc(memory.first_seen_at),
        reverse=True,
    )[:FEEDBACK_QUOTES]
    for memory in strong:
        negative = polarity(memory) < 0
        yield Change(
            "feedback",
            "negative" if negative else "positive",
            f"{'Negative feedback' if negative else 'Praise'}: {memory.content}",
            ensure_utc(memory.first_seen_at),
            after=memory.content,
            evidence=[memory.id],
            detail={"polarity": round(polarity(memory), 2)},
            subject=memory.id,
        )

    # Counts are numbers about the customer, not quotes from them: shown to every reader.
    now = sum(1 for memory in feedback if is_negative(memory))
    before = sum(1 for memory in inputs.prior_feedback if is_negative(memory))
    detail = {"negative": now, "negative_before": before}
    if now > before and now >= 2:
        yield Change(
            "feedback", "rose", f"Negative feedback increased: {now} against {before} in the period before",
            inputs.until, before=str(before), after=str(now), detail=detail,
        )
    elif before >= 2 and now < before:
        yield Change(
            "feedback", "fell", f"Negative feedback eased: {now} against {before} in the period before",
            inputs.until, before=str(before), after=str(now), detail=detail,
        )


# ------------------------------------------------------------------ versions


def _within(moment: datetime, inputs: ChangeInputs) -> bool:
    return inputs.since <= ensure_utc(moment) <= inputs.until


def _from_versions(inputs: ChangeInputs) -> Iterable[Change]:
    recurring: dict[str, tuple[Any, Any, int]] = {}
    for version, memory in inputs.versions:
        reason = str(version.reason or "")
        if str(memory.type) == "problem" and (reason == "repeated_evidence" or reason.startswith("consolidation_")):
            # Several reports in one window are one change: "reported again (3 times)".
            times = recurring.get(memory.id, (None, None, 0))[2]
            recurring[memory.id] = (version, memory, times + 1)
        elif reason == "feedback_corrected":
            replacement = inputs.related.get(str(memory.superseded_by or ""))
            if replacement is None:
                continue
            yield Change(
                "memory", "corrected", f"Corrected by a person: {replacement.content}",
                ensure_utc(version.created_at),
                before=memory.content, after=replacement.content,
                evidence=[replacement.id, memory.id], source="version",
                detail={"memory_type": str(memory.type)},
                subject=replacement.id, before_id=memory.id,
            )
    for version, memory, times in recurring.values():
        if _within(memory.first_seen_at, inputs):
            continue  # first reported in the window: already a change, not "again"
        if str(memory.status) != "active" or _meta(memory).get("resolved"):
            continue
        yield Change(
            "problem", "recurring",
            f"Reported again{f' ({times} times)' if times > 1 else ''}: {memory.content}",
            ensure_utc(version.created_at),
            after=memory.content, evidence=[memory.id], source="version",
            detail={"times": times, "evidence_count": memory.evidence_count},
            subject=memory.id,
        )


# ---------------------------------------------------------------------- goals

# evidence kind -> (change kind, title, the status it left the goal in)
_GOAL_KINDS = {
    "stated": ("opened", "Goal set", "open"),
    "progress": ("progressed", "Progress on a goal", "progressing"),
    "achieved": ("achieved", "Goal achieved", "achieved"),
    "abandoned": ("abandoned", "Goal abandoned", "abandoned"),
    "stalled": ("stalled", "Goal stalled", "stalled"),
    "override": ("changed", "Goal status set by a person", None),
}


def _parse(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return fallback
    return fallback


def _from_goals(inputs: ChangeInputs) -> Iterable[Change]:
    for goal in inputs.goals:
        latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
        for entry in getattr(goal, "evidence", None) or []:
            kind = str(entry.get("kind", ""))
            when = _parse(entry.get("at"), inputs.until)
            if kind in _GOAL_KINDS and _within(when, inputs):
                latest[kind] = (when, entry)  # one change per kind per goal per window
        for kind, (when, entry) in latest.items():
            change_kind, title, status = _GOAL_KINDS[kind]
            before = entry.get("from") if kind == "override" else None
            after = entry.get("to") if kind == "override" else status
            yield Change(
                "goal", change_kind, f"{title}: {goal.statement}", when,
                before=_words(before) if before else None,
                after=_words(after) if after else None,
                evidence=[goal.id, *([entry["memory_id"]] if entry.get("memory_id") else [])],
                source="goal",
                detail={"goal_id": goal.id, "progress": round(float(goal.progress or 0), 2)},
                subject=goal.id,
            )


# ------------------------------------------------------------------ lifecycle


# Automatic steps taken in the same evaluation as a track's first placement are part of
# placing the customer — a track switched on today is not news about them. One evaluation
# writes its rows within milliseconds; a person's change is always news.
PLACEMENT_SECONDS = 1.0


def _from_states(inputs: ChangeInputs) -> Iterable[Change]:
    placed: dict[str, datetime] = {}
    for row, _ in inputs.states:
        if not row.previous_state:
            placed[getattr(row, "track", None) or "lifecycle"] = ensure_utc(row.entered_at)
    for row, evaluation in inputs.states:
        track = getattr(row, "track", None) or "lifecycle"
        if not row.previous_state:
            continue
        if (
            str(row.source) == "auto"
            and track in placed
            and abs((ensure_utc(row.entered_at) - placed[track]).total_seconds()) <= PLACEMENT_SECONDS
        ):
            continue
        label = inputs.track_labels.get(track, _words(track).title())
        manual = str(row.source) == "manual"
        if evaluation:
            words = reasons_in_words(evaluation)
        elif manual:
            words = [row.reason] if row.reason else ["set by hand"]
        else:
            words = []
        # The transition's name only when it says something the target state does not.
        if manual:
            how = " (set by hand)"
        elif row.transition and row.transition != row.state:
            how = f" ({_words(row.transition)})"
        else:
            how = ""
        yield Change(
            "lifecycle", "moved",
            f"{label}: {_words(row.previous_state)} → {_words(row.state)}{how}",
            ensure_utc(row.entered_at),
            before=_words(row.previous_state),
            after=_words(row.state),
            evidence=list(getattr(row, "evidence", None) or [])[:10],
            source="lifecycle",
            track=track,
            reasons=words,
            detail={"transition": row.transition, "manual": manual},
        )


# ------------------------------------------------------------------ snapshots


def _value(values: dict[str, Any] | None, name: str) -> Any:
    if not values:
        return None
    value = values.get(name)
    return None if value == WITHHELD else value


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _from_snapshots(inputs: ChangeInputs, *, already: set[tuple[str, str]]) -> Iterable[Change]:
    if inputs.then is None:
        return
    then, now = inputs.then, inputs.now

    def at(*facts: str) -> datetime:
        found = [inputs.moments[fact] for fact in facts if fact in inputs.moments]
        return max(found) if found else inputs.until

    band_before, band_after = _value(then, "health.band"), _value(now, "health.band")
    score_before, score_after = _number(_value(then, "health.score")), _number(_value(now, "health.score"))
    if band_before and band_after and band_before != band_after:
        yield Change(
            "health", "crossed", f"Health moved from {_words(band_before)} to {_words(band_after)}",
            at("health.band"),
            before=_words(band_before) + (f" ({score_before:.0f})" if score_before is not None else ""),
            after=_words(band_after) + (f" ({score_after:.0f})" if score_after is not None else ""),
            source="snapshot",
            detail={"score_before": score_before, "score_after": score_after},
        )
    elif score_before is not None and score_after is not None and abs(score_after - score_before) >= HEALTH_POINTS:
        delta = score_after - score_before
        yield Change(
            "health", "rose" if delta > 0 else "fell",
            f"Health {'up' if delta > 0 else 'down'} {abs(delta):.0f} points to {score_after:.0f}",
            at("health.score"), before=f"{score_before:.0f}", after=f"{score_after:.0f}", source="snapshot",
        )

    risk_before, risk_after = _number(_value(then, "signals.churn_risk")), _number(_value(now, "signals.churn_risk"))
    if risk_before is not None and risk_after is not None and abs(risk_after - risk_before) >= RISK_STEP:
        delta = risk_after - risk_before
        yield Change(
            "risk", "rose" if delta > 0 else "fell",
            f"Churn risk {'rose' if delta > 0 else 'fell'} from {risk_before:.0%} to {risk_after:.0%}",
            at("signals.churn_risk"), before=f"{risk_before:.2f}", after=f"{risk_after:.2f}", source="snapshot",
        )

    trajectory_before, trajectory_after = _value(then, "signals.trajectory"), _value(now, "signals.trajectory")
    if trajectory_before and trajectory_after and trajectory_before != trajectory_after:
        yield Change(
            "trajectory", "changed", f"Trajectory now {_words(trajectory_after)}, was {_words(trajectory_before)}",
            at("signals.trajectory"), before=_words(trajectory_before), after=_words(trajectory_after),
            source="snapshot",
        )

    # The plan and the preferred channel come from memories, which say more; the snapshots
    # speak only when no memory in the window did (a restatement merged into an older one).
    plan_before, plan_after = _value(then, "subscription.plan"), _value(now, "subscription.plan")
    if (
        plan_before and plan_after and plan_before != plan_after
        and not {("subscription", "changed"), ("subscription", "cancelled")} & already
    ):
        yield Change(
            "subscription", "changed", f"Plan changed from {_plan_name(plan_before)} to {_plan_name(plan_after)}",
            at("subscription.plan"), before=_words(plan_before), after=_words(plan_after), source="snapshot",
            detail={"plan": plan_after, "previous_plan": plan_before},
        )
    channel_before, channel_after = _value(then, "preferences.channel"), _value(now, "preferences.channel")
    if channel_before and channel_after and channel_before != channel_after and ("preference", "changed") not in already:
        yield Change(
            "preference", "changed", f"Preferred channel changed from {channel_before} to {channel_after}",
            at("preferences.channel"), before=str(channel_before), after=str(channel_after), source="snapshot",
        )


# -------------------------------------------------------------------- signals


def _from_signals(inputs: ChangeInputs) -> Iterable[Change]:
    if inputs.then is None:
        return
    before = set(_value(inputs.then, "signals.active") or [])
    after = set(_value(inputs.now, "signals.active") or [])
    when = inputs.moments.get("signals.active", inputs.until)
    for key in sorted(after - before):
        label = inputs.signal_labels.get(key) or _words(key)
        yield Change(
            "signal", "started", f"Signal started: {label}", when, after=label, source="signal", detail={"key": key}
        )
    for key in sorted(before - after):
        label = inputs.signal_labels.get(key) or _words(key)
        yield Change(
            "signal", "stopped", f"Signal stopped: {label}", when, before=label, source="signal", detail={"key": key}
        )


# ------------------------------------------------------------------- activity


def _from_activity(inputs: ChangeInputs) -> Iterable[Change]:
    prior_start = inputs.since - (inputs.until - inputs.since)
    if inputs.customer_since is not None and ensure_utc(inputs.customer_since) > prior_start:
        # "None in the period before" is not news about a customer who did not exist then.
        return
    now, before = inputs.events_now, inputs.events_before
    detail: dict[str, Any] = {"events": now, "events_before": before}
    if before >= 3 and now == 0:
        yield Change(
            "activity", "quiet", f"Went quiet: no events, against {before} in the period before",
            inputs.until, before=str(before), after="0", source="activity", detail=detail,
        )
    elif before == 0 and now >= 3:
        yield Change(
            "activity", "rose", f"Became active: {now} events, none in the period before",
            inputs.until, before="0", after=str(now), source="activity", detail=detail,
        )
    elif before >= 3 and abs(now - before) >= 3:
        change = (now - before) / before * 100
        if abs(change) >= ACTIVITY_STEP:
            yield Change(
                "activity", "rose" if change > 0 else "fell",
                f"Activity {'up' if change > 0 else 'down'} {abs(change):.0f}%: {now} events against {before}",
                inputs.until, before=str(before), after=str(now), source="activity",
                detail={**detail, "change_pct": round(change, 1)},
            )


# -------------------------------------------------------------------- summary


def summarise(changes: Sequence[Change], *, lead: str) -> str:
    """One sentence a person can read before a call: "In the last 7 days: …"."""
    if not changes:
        return f"{lead}: nothing material changed."
    by_kind: dict[tuple[str, str], list[Change]] = {}
    for change in changes:
        by_kind.setdefault((change.type, change.kind), []).append(change)

    def first(type_: str, *kinds: str) -> Change | None:
        for kind in kinds:
            if by_kind.get((type_, kind)):
                return by_kind[(type_, kind)][0]  # the newest
        return None

    def count(key: tuple[str, str], one: str, many: str) -> str | None:
        number = len(by_kind.get(key, []))
        return None if not number else (one if number == 1 else many.format(n=number))

    parts: list[str | None] = []
    subscription = first("subscription", "cancelled", "changed", "renewed")
    if subscription is not None:
        parts.append(_lower_first(subscription.title))
    parts.append(count(("problem", "opened"), "a new problem", "{n} new problems"))
    parts.append(count(("problem", "resolved"), "a problem resolved", "{n} problems resolved"))
    parts.append(count(("problem", "recurring"), "a problem reported again", "{n} problems reported again"))
    kinds = [kind for change in by_kind.get(("intent", "expressed"), []) for kind in change.detail.get("kinds", [])]
    if kinds:
        parts.append(sentence("intents.kinds", list(dict.fromkeys(kinds))))
    elif by_kind.get(("intent", "expressed")):
        parts.append("a new intent")
    moved: set[str] = set()
    for change in by_kind.get(("lifecycle", "moved"), []):
        track = change.track or "lifecycle"
        if track in moved:
            continue
        moved.add(track)
        parts.append(f"{'' if track == 'lifecycle' else _words(track) + ' '}moved to {change.after}")
    health = first("health", "crossed", "fell", "rose")
    if health is not None:
        parts.append(_lower_first(health.title))
    preference = first("preference", "changed")
    if preference is not None:
        parts.append(_lower_first(preference.title) if preference.source == "snapshot" else "a preference changed")
    parts.append(count(("goal", "achieved"), "a goal achieved", "{n} goals achieved"))
    parts.append(count(("goal", "stalled"), "a goal stalled", "{n} goals stalled"))
    parts.append(count(("goal", "abandoned"), "a goal abandoned", "{n} goals abandoned"))
    parts.append(count(("goal", "opened"), "a new goal", "{n} new goals"))
    if first("feedback", "rose") is not None:
        parts.append("negative feedback increasing")
    else:
        parts.append(count(("feedback", "negative"), "negative feedback", "{n} pieces of negative feedback"))
    activity = first("activity", "quiet", "fell", "rose")
    if activity is not None:
        parts.append(activity.title.split(":")[0].lower())
    words = [part for part in parts if part]
    if not words:
        words = [f"{len(changes)} smaller change{'s' if len(changes) != 1 else ''}"]
    return f"{lead}: {'; '.join(words)}."


def counts(changes: Sequence[Change]) -> dict[str, int]:
    found: dict[str, int] = {}
    for change in changes:
        found[change.type] = found.get(change.type, 0) + 1
    return found


# -------------------------------------------------------------- then and now

STATE_FIELDS = (
    ("plan", "subscription.plan"),
    ("health_score", "health.score"),
    ("health_band", "health.band"),
    ("lifecycle", "state.current"),
    ("trajectory", "signals.trajectory"),
    ("churn_risk", "signals.churn_risk"),
    ("expansion_score", "signals.expansion_score"),
    ("open_problems", "problems.open_count"),
    ("open_goals", "goals.open_count"),
    ("stalled_goals", "goals.stalled_count"),
    ("achieved_goals", "goals.achieved_count"),
    ("preferred_channel", "preferences.channel"),
    ("intents", "intents.kinds"),
    ("activity_trend", "activity.trend"),
    ("signals", "signals.active"),
)


def state_of(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """The customer at one moment, as a small document — one side of "then vs now"."""
    if values is None:
        return None
    state: dict[str, Any] = {name: values.get(fact) for name, fact in STATE_FIELDS}
    state["tracks"] = {
        name[len(LIFECYCLE_PREFIX) :]: value
        for name, value in sorted(values.items())
        if name.startswith(LIFECYCLE_PREFIX) and not name.endswith(DAYS_SUFFIX)
    }
    return state


def fact_diff(then: dict[str, Any] | None, now: dict[str, Any]) -> list[dict[str, Any]]:
    """Fact by fact, what differs between two documents a reader may see."""
    if then is None:
        return []
    tracks = sorted(
        {name for name in (*then, *now) if name.startswith(LIFECYCLE_PREFIX) and not name.endswith(DAYS_SUFFIX)}
    )
    found: list[dict[str, Any]] = []
    for name in (*TRACKED, *tracks):
        before, after = then.get(name), now.get(name)
        if WITHHELD in (before, after) or _same(before, after):
            continue
        entry: dict[str, Any] = {"fact": name, "before": before, "after": after}
        if isinstance(before, list) or isinstance(after, list):
            old, new = set(map(str, before or [])), set(map(str, after or []))
            entry["added"], entry["removed"] = sorted(new - old), sorted(old - new)
        found.append(entry)
    return found


def _same(before: Any, after: Any) -> bool:
    if isinstance(before, list) or isinstance(after, list):
        return sorted(map(str, before or [])) == sorted(map(str, after or []))
    if isinstance(before, float) or isinstance(after, float):
        try:
            return round(float(before), 3) == round(float(after), 3)
        except (TypeError, ValueError):
            return False
    return before == after
