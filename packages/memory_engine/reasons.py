"""A condition's decisive clauses, as the reasons a person would give.

An evaluation trace says `problems.open_count >= 3: true (is 3)`. That is precise and it is
what a rule author needs. It is not what a support lead reads on a customer's page, or what
a webhook receiver shows in Slack: they want "3 unresolved problems", "activity down 47%",
"negative feedback increasing" (§26 4.2).

Each sentence describes the *actual* value a clause read, never the threshold it was
compared with — which makes it true whichever way the clause went: under a `not`, a false
clause "plan is enterprise" still reads, correctly, "on the pro plan". Clauses with no
value say nothing rather than guess.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from memory_engine.facts import (
    CATALOG,
    DAYS_SUFFIX,
    LIFECYCLE_PREFIX,
    METADATA_PREFIX,
    REQUEST_PREFIX,
)


def _plural(count: float, word: str, plural: str | None = None) -> str:
    number = int(count) if float(count).is_integer() else round(float(count), 1)
    return f"{number} {word if number == 1 else (plural or word + 's')}"


def _days(value: float, what: str) -> str:
    return f"{what} for {_plural(value, 'day')}"


def _words(value: Any) -> str:
    return str(value).replace("_", " ")


def _listing(value: Any) -> str:
    items = [str(item).replace("_", " ") for item in (value or [])]
    if not items:
        return "none"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _change_pct(value: float) -> str:
    if value < 0:
        return f"activity down {abs(value):.0f}%"
    if value > 0:
        return f"activity up {value:.0f}%"
    return "activity unchanged"


_INTENT_WORDS = {
    "cancellation": "said they may cancel",
    "downgrade": "talked about downgrading",
    "expansion": "talked about expanding",
    "evaluation": "is evaluating",
    "integration": "is integrating",
    "migration": "is migrating",
    "renewal": "talked about renewing",
    "purchase": "asked about buying",
}

_OPT_OUT_WORDS = {
    "contact": "asked not to be contacted",
    "phone": "asked not to be called",
    "email": "asked not to be emailed",
    "sms": "asked not to be texted",
    "whatsapp": "asked not to be messaged on WhatsApp",
    "sales": "asked for no sales outreach",
    "marketing": "opted out of marketing",
}

# fact -> sentence for its actual value. Everything not listed falls back to the fact's
# catalogue description.
_TEMPLATES: dict[str, Callable[[Any], str | None]] = {
    "health.score": lambda v: f"health score {float(v):.0f}",
    "health.band": lambda v: f"health is {_words(v)}",
    "health.churn_risk": lambda v: f"churn risk {float(v):.2f}",
    "signals.trajectory": lambda v: f"trajectory is {_words(v)}",
    "signals.churn_risk": lambda v: f"forecast churn risk {float(v):.2f}",
    "signals.expansion_score": lambda v: f"expansion likelihood {float(v):.2f}",
    "signals.active": lambda v: f"signals firing: {_listing(v)}" if v else "no signals firing",
    "signals.risks": lambda v: f"risk signals: {_listing(v)}" if v else "no risk signals",
    "signals.opportunities": lambda v: f"opportunity signals: {_listing(v)}" if v else "no opportunity signals",
    "state.current": lambda v: f"lifecycle is {_words(v)}",
    "state.days_in_state": lambda v: _days(v, "in the current state"),
    "subscription.plan": lambda v: f"on the {_words(v)} plan",
    "subscription.previous_plan": lambda v: f"previously on the {_words(v)} plan",
    "subscription.direction": lambda v: f"subscription {_words(v)}",
    "subscription.changed_days_ago": lambda v: f"plan changed {_plural(v, 'day')} ago",
    "problems.open_count": lambda v: _plural(v, "unresolved problem"),
    "problems.recent_count": lambda v: f"{_plural(v, 'problem')} reported recently",
    "problems.entities": lambda v: f"open problems mention {_listing(v)}" if v else "no open problems name a product",
    "problems.oldest_open_days": lambda v: f"oldest open problem is {_plural(v, 'day')} old",
    "problems.max_repeats": lambda v: f"a problem reported {_plural(v, 'time')}",
    "goals.open_count": lambda v: _plural(v, "open goal"),
    "goals.progressing_count": lambda v: _plural(v, "goal") + " progressing",
    "goals.stalled_count": lambda v: _plural(v, "stalled goal"),
    "goals.achieved_count": lambda v: _plural(v, "goal") + " achieved",
    "goals.abandoned_count": lambda v: _plural(v, "goal") + " abandoned",
    "preferences.channel": lambda v: f"prefers {v}",
    "preferences.channels": lambda v: f"preferred channels: {_listing(v)}" if v else "no preferred channel",
    "preferences.opt_outs": lambda v: "; ".join(_OPT_OUT_WORDS.get(str(k), str(k)) for k in v) if v else "no opt-outs",
    "intents.kinds": lambda v: "; ".join(_INTENT_WORDS.get(str(k), str(k)) for k in v) if v else "no stated intent",
    "intents.latest_kind": lambda v: _INTENT_WORDS.get(str(v), f"latest intent: {_words(v)}"),
    "intents.latest_days_ago": lambda v: f"latest intent {_plural(v, 'day')} ago",
    "activity.last_event_days_ago": lambda v: f"no activity for {_plural(v, 'day')}" if v >= 1 else "active today",
    "activity.events_recent": lambda v: f"{_plural(v, 'event')} in the last 14 days",
    "activity.events_prior": lambda v: f"{_plural(v, 'event')} in the 14 days before",
    "activity.trend": lambda v: f"activity is {_words(v)}",
    "activity.change_pct": _change_pct,
    "activity.distinct_features": lambda v: f"uses {_plural(v, 'feature')}",
    "feedback.count": lambda v: _plural(v, "piece") + " of feedback",
    "feedback.negative_count": lambda v: _plural(v, "negative piece") + " of feedback",
    "feedback.recent_negative_count": lambda v: _plural(v, "negative piece") + " of feedback in 14 days",
    "feedback.negative_trend": lambda v: {
        "rising": "negative feedback increasing",
        "falling": "negative feedback decreasing",
    }.get(str(v), "negative feedback steady"),
    "customer.age_days": lambda v: f"a customer for {_plural(v, 'day')}",
    "request.amount": lambda v: f"amount {float(v):g}",
    "request.channel": lambda v: f"via {v}",
    "request.plan": lambda v: f"the {_words(v)} plan",
    "memories.restricted_count": lambda v: _plural(v, "restricted memory", "restricted memories"),
}

# Content facts whose words must not appear in a sentence shown to a reader who may not
# see everything — the same families redaction treats as content (§17c).
_CONTENT_FAMILIES = frozenset({"problems", "goals", "preferences", "intents", "subscription", "feedback"})


def sentence(fact: str, actual: Any) -> str | None:
    """The actual value of ``fact`` in words, or ``None`` when there is no value."""
    if actual is None or actual == "" or actual == "[withheld]":
        return None
    template = _TEMPLATES.get(fact)
    try:
        if template is not None:
            return template(actual)
        if fact.startswith(LIFECYCLE_PREFIX):
            track = fact[len(LIFECYCLE_PREFIX) :]
            if track.endswith(DAYS_SUFFIX):
                return _days(float(actual), f"in the current {track.removesuffix(DAYS_SUFFIX)} state")
            return f"{track} is {_words(actual)}"
        if fact.startswith(METADATA_PREFIX):
            return f"{fact[len(METADATA_PREFIX):].replace('.', ' ')} is {actual}"
        if fact.startswith(REQUEST_PREFIX):
            return f"{fact[len(REQUEST_PREFIX):]} is {actual}"
    except (TypeError, ValueError):
        return None
    spec = CATALOG.get(fact)
    described = spec.description.split(" — ")[0].rstrip(".") if spec else fact
    if isinstance(actual, list):
        return f"{described.lower()}: {_listing(actual)}"
    return f"{described.lower()}: {actual}"


def reasons(evaluation: Any, *, limit: int = 6) -> list[str]:
    """Sentences for an evaluation's decisive clauses, in order, without repeats.

    Accepts an :class:`memory_engine.conditions.Evaluation` or its stored ``as_dict`` form,
    so reasons can be rendered for transitions recorded before this existed.
    """
    if evaluation is None:
        return []
    if isinstance(evaluation, dict):
        leaves = [(leaf.get("fact", ""), leaf.get("actual")) for leaf in evaluation.get("decisive") or []]
    else:
        leaves = [(leaf.fact, leaf.actual) for leaf in getattr(evaluation, "decisive", [])]
    out: list[str] = []
    for fact, actual in leaves:
        text = sentence(str(fact), actual)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def is_content(fact: str) -> bool:
    return fact.split(".", 1)[0] in _CONTENT_FAMILIES
