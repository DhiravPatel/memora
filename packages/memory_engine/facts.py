"""Everything a rule can ask about a customer, as one typed document.

Guardrails, the lifecycle state machine, workflows, feature flags and cohorts all need to
ask the same kind of question — *is this customer's health below 60 and do they have an
open Shopify problem?* — and each of them, built alone, would grow its own dialect and its
own opinion of what "open problem" means. This module is the single answer: a catalog of
named facts with types, and a builder that computes their values from what the engine
already knows.

Three properties matter:

* **Every fact has a type**, declared in :data:`CATALOG`, so a condition like
  ``health.score contains "x"`` is refused when it is written rather than quietly false
  forever after.
* **Facts carry evidence.** ``problems.entities`` knows which memories mention Shopify, so
  a guardrail that refuses an upsell can cite them instead of asserting.
* **Facts are derived, never stored as truth.** The builder is a pure function over the
  memories, goals, health and signals it is handed; the service that gathers those inputs
  reuses the services that own them, so a fact and the endpoint it came from never differ.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.time import ensure_utc, utcnow
from nlp.entities import channel_stance
from nlp.intents import KINDS as INTENT_KINDS
from nlp.intents import intent_kinds
from nlp.lexicon import PLANS
from nlp.optouts import KINDS as OPT_OUT_KINDS
from nlp.optouts import opt_outs
from nlp.tokenize import root, surface_words, tokenize

METADATA_PREFIX = "customer.metadata."
# The action an agent proposes, for guardrail rules: `request.amount > 500`. Present only in
# a guardrail check's fact document; anywhere else these facts are unknown.
REQUEST_PREFIX = "request."
# One fact per lifecycle track (§26 4.2): `lifecycle.engagement == "at_risk"`, and
# `lifecycle.engagement.days_in_state`. Tracks are project-defined, so like metadata these
# need no registering; the primary track is also `state.current`.
LIFECYCLE_PREFIX = "lifecycle."
DAYS_SUFFIX = ".days_in_state"
# How far back "recent" reaches for trends — the same window the signals use.
RECENT_DAYS = 14
# The customer's action history (§26 4.5), per action and per family:
# `actions.issue_credit.count_30d`, `actions.money.amount_30d`, `actions.contact.count_7d`,
# `actions.offer_upgrade.days_since_last`. Counts and amounts read 0 with no history, so a
# rule like `actions.issue_credit.count_30d >= 2` is false — not unknown — for a customer
# never credited.
ACTIONS_PREFIX = "actions."
ACTION_METRICS = ("count_7d", "count_30d", "amount_30d", "days_since_last")
ZERO_ACTION_METRICS = ("count_7d", "count_30d", "amount_30d")
ACTION_HISTORY_DAYS = 90

# The types a fact can have, and what each means to the condition language.
#   number  — comparable (<, >, between)
#   string  — equality and substring
#   enum    — a string with a closed set of values, checked when a condition is written
#   boolean — equality only
#   list    — a set of labels; ``contains`` is exact membership, case-insensitive
#   terms   — a set of lemmas; ``contains "billing issue"`` means every word is present,
#             in any inflection, which is what a person writing a rule means by "mentions"
#   any     — customer metadata, whose type is whatever the customer sent
FACT_TYPES = ("number", "string", "enum", "boolean", "list", "terms", "any")


@dataclass(frozen=True, slots=True)
class FactSpec:
    name: str
    type: str
    description: str
    values: tuple[str, ...] = ()
    unit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "values": list(self.values),
            "unit": self.unit,
        }


def _spec(name: str, type: str, description: str, *, values: Sequence[str] = (), unit: str | None = None) -> FactSpec:
    return FactSpec(name=name, type=type, description=description, values=tuple(values), unit=unit)


CATALOG: dict[str, FactSpec] = {
    spec.name: spec
    for spec in (
        # --------------------------------------------------------------- who
        _spec("customer.external_id", "string", "Your own id for the customer"),
        _spec("customer.name", "string", "The customer's display name"),
        _spec("customer.email", "string", "The customer's email address"),
        _spec("customer.email_domain", "string", "The domain of the customer's email — useful for company-level rules"),
        _spec("customer.age_days", "number", "Days since the customer was first seen", unit="days"),
        # ------------------------------------------------------------ health
        _spec("health.score", "number", "Health score, 0–100, from memory alone", unit="points"),
        _spec("health.band", "enum", "Health band", values=("healthy", "watch", "at_risk", "critical")),
        _spec("health.churn_risk", "number", "Churn risk implied by health, 0–1"),
        # ----------------------------------------------------------- signals
        _spec("signals.trajectory", "enum", "Which way the customer is moving", values=("improving", "steady", "declining")),
        _spec("signals.churn_risk", "number", "Forecast churn risk, 0–1"),
        _spec("signals.expansion_score", "number", "Forecast expansion likelihood, 0–1"),
        _spec("signals.confidence", "number", "How much evidence the forecast rests on, 0–1"),
        _spec("signals.risk_pressure", "number", "Combined weight of every risk signal firing"),
        _spec("signals.active", "list", "Keys of every signal currently firing, e.g. repeat_problem"),
        _spec("signals.risks", "list", "Keys of the risk signals firing"),
        _spec("signals.opportunities", "list", "Keys of the opportunity signals firing"),
        # ------------------------------------------------------------- state
        _spec("state.current", "string", "Lifecycle state, from the project's state machine"),
        _spec("state.days_in_state", "number", "Days since the customer entered the current state", unit="days"),
        _spec("state.pinned", "boolean", "Whether a person pinned the state by hand"),
        # ------------------------------------------------------ subscription
        _spec("subscription.plan", "string", "Current plan, lowercase — the newest subscription statement"),
        _spec("subscription.previous_plan", "string", "The plan before the latest change"),
        _spec(
            "subscription.direction",
            "enum",
            "What the latest subscription change was",
            values=("upgraded", "downgraded", "cancelled", "renewed", "started", "changed"),
        ),
        _spec("subscription.changed_days_ago", "number", "Days since the latest subscription change", unit="days"),
        # ---------------------------------------------------------- problems
        _spec("problems.open_count", "number", "Open problems"),
        _spec("problems.recent_count", "number", "Problems reported in the recent signal window"),
        _spec("problems.entities", "list", "Products, integrations and features the open problems mention, lowercase"),
        _spec("problems.terms", "terms", "Words used in open problems — `contains \"billing\"` matches any inflection"),
        _spec("problems.oldest_open_days", "number", "Age of the oldest open problem", unit="days"),
        _spec("problems.max_repeats", "number", "Most times any one open problem has been reported"),
        # ------------------------------------------------------------- goals
        _spec("goals.open_count", "number", "Goals stated and not yet progressing"),
        _spec("goals.progressing_count", "number", "Goals with evidence of progress"),
        _spec("goals.stalled_count", "number", "Goals with no evidence for 30 days"),
        _spec("goals.achieved_count", "number", "Goals the customer reached"),
        _spec("goals.abandoned_count", "number", "Goals the customer walked away from"),
        _spec("goals.terms", "terms", "Words used in open and progressing goals"),
        # ------------------------------------------------------- preferences
        _spec("preferences.channel", "string", "Preferred contact channel, lowercase — the newest preference naming one"),
        _spec("preferences.channels", "list", "Every contact channel any preference names"),
        _spec("preferences.terms", "terms", "Words used in stated preferences"),
        _spec(
            "preferences.opt_outs",
            "list",
            "What the customer asked not to receive: contact, phone, email, sms, whatsapp, sales, marketing",
            values=OPT_OUT_KINDS,
        ),
        # ----------------------------------------------------------- intents
        _spec("intents.kinds", "list", "What the customer intends", values=INTENT_KINDS),
        _spec("intents.latest_kind", "enum", "The most recent intent", values=INTENT_KINDS),
        _spec("intents.latest_days_ago", "number", "Days since the most recent intent", unit="days"),
        # ---------------------------------------------------------- activity
        _spec("activity.last_event_days_ago", "number", "Days since the last event", unit="days"),
        _spec("activity.events_recent", "number", "Events in the recent signal window (14 days)"),
        _spec("activity.events_prior", "number", "Events in the 14 days before that"),
        _spec("activity.trend", "enum", "How activity is moving", values=("growing", "steady", "declining", "silent")),
        _spec(
            "activity.change_pct",
            "number",
            "Recent events against the 14 days before, in percent — -47 is activity down 47%",
            unit="%",
        ),
        _spec("activity.distinct_features", "number", "Distinct product features the customer has used"),
        # ---------------------------------------------------------- feedback
        _spec("feedback.count", "number", "Feedback memories"),
        _spec("feedback.negative_count", "number", "Feedback memories with negative sentiment"),
        _spec("feedback.recent_negative_count", "number", "Negative feedback first seen in the last 14 days"),
        _spec("feedback.prior_negative_count", "number", "Negative feedback first seen in the 14 days before that"),
        _spec(
            "feedback.negative_trend",
            "enum",
            "Whether negative feedback is increasing",
            values=("rising", "steady", "falling"),
        ),
        # ---------------------------------------------------------- memories
        # --------------------------------------------------- the proposed action
        _spec("request.action", "string", "In a guardrail check: the action the agent proposes"),
        _spec("request.channel", "string", "In a guardrail check: the channel it would use, lowercase"),
        _spec("request.amount", "number", "In a guardrail check: an amount, e.g. a discount or refund"),
        _spec("request.topic", "terms", "In a guardrail check: what the action is about, e.g. a ticket's subject"),
        _spec("request.plan", "string", "In a guardrail check: a plan the action involves, lowercase"),
        _spec("memories.count", "number", "Active memories, restricted ones included"),
        _spec("memories.restricted_count", "number", "Active memories the restriction policy marked restricted"),
    )
}

# Health factor keys and signal keys are not evidence ids; everything below maps a fact
# to the memory, goal or signal ids that justify its value.


@dataclass(slots=True)
class CustomerFacts:
    """The fact document for one customer, with the evidence behind each value."""

    values: dict[str, Any] = field(default_factory=dict)
    # fact -> ids that justify it
    evidence: dict[str, list[str]] = field(default_factory=dict)
    # list/terms fact -> element -> ids, so ``contains "shopify"`` can cite exactly the
    # memories that mention Shopify rather than every open problem
    evidence_by_value: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=utcnow)
    # Memory and goal ids that are restricted (§17c). Facts are computed over everything —
    # a guardrail must see a restricted open problem — and :meth:`redacted` is what a
    # reader without clearance is shown.
    restricted_ids: set[str] = field(default_factory=set)
    # Facts a redacted view had to change, so the reader is told rather than misled.
    withheld_facts: list[str] = field(default_factory=list)
    # On a redacted view: the ids it was redacted for, so evidence chosen later (a
    # guardrail's reason, say) can be filtered by the same rule.
    hidden_ids: frozenset[str] = frozenset()

    def get(self, name: str) -> Any:
        if name.startswith(ACTIONS_PREFIX) and name not in self.values and name.endswith(ZERO_ACTION_METRICS):
            return 0
        if name.startswith(REQUEST_PREFIX) and name not in self.values:
            # Beyond the catalogued request facts, any key the caller sent is addressable.
            extra = self.values.get("request.extra") or {}
            return extra.get(name[len(REQUEST_PREFIX):])
        if name.startswith(METADATA_PREFIX):
            node: Any = self.values.get("customer.metadata") or {}
            for part in name[len(METADATA_PREFIX) :].split("."):
                if not isinstance(node, dict) or part not in node:
                    return None
                node = node[part]
            return node
        return self.values.get(name)

    def evidence_for(self, name: str, value: Any = None) -> list[str]:
        if value is not None and name in self.evidence_by_value:
            by_value = self.evidence_by_value[name]
            key = str(value).strip().lower()
            if key in by_value:
                return list(by_value[key])
            # A terms fact is keyed by root, and a phrase cites what matched its words.
            roots = [root(word) for word in surface_words(key)] or [root(key)]
            ids = [ident for key_root in roots for ident in by_value.get(key_root, [])]
            if ids:
                return list(dict.fromkeys(ids))
        return list(self.evidence.get(name, []))

    def cited_ids(self) -> set[str]:
        """Every memory and goal id any fact cites."""
        ids = {ident for values in self.evidence.values() for ident in values}
        for index in self.evidence_by_value.values():
            for values in index.values():
                ids.update(values)
        return ids

    def redacted(self, hidden: Iterable[str] | None = None) -> CustomerFacts:
        """The document as a reader without clearance may see it.

        ``hidden`` overrides what is withheld — the ids a particular reader may not see,
        which for an agent profile limited to some memory types is more than the
        restricted ones. Omitted, it is the restricted ids.

        Aggregates stay whole — a count or a score is a number about a customer, not a
        quote from one, the same rule health follows (§17c). Content-derived facts lose
        exactly the parts that came *only* from restricted memories: ``problems.entities``
        of ``["shopify", "hr"]`` becomes ``["shopify"]`` when "hr" was only ever mentioned
        in a restricted memory, and a preferred channel known only from a restricted
        preference becomes unknown.
        """
        hidden = frozenset(self.restricted_ids if hidden is None else hidden)
        if not hidden:
            return self
        values = dict(self.values)
        withheld: list[str] = []

        for name, value in self.values.items():
            spec = CATALOG.get(name)
            if spec is None or name.split(".", 1)[0] in _AGGREGATE_FAMILIES:
                continue
            if spec.type in ("list", "terms") and isinstance(value, list):
                index = self.evidence_by_value.get(name, {})
                kept = [
                    item
                    for item in value
                    if not (ids := _ids_for(index, spec.type, item)) or any(i not in hidden for i in ids)
                ]
                if kept != value:
                    values[name] = kept
                    withheld.append(name)
            elif spec.type in ("string", "enum") and value is not None:
                ids = self.evidence.get(name, [])
                if ids and all(ident in hidden for ident in ids):
                    values[name] = None
                    withheld.append(name)

        evidence = {
            name: [ident for ident in ids if ident not in hidden]
            for name, ids in self.evidence.items()
        }
        by_value = {
            name: {
                key: kept
                for key, ids in index.items()
                if (kept := [ident for ident in ids if ident not in hidden])
            }
            for name, index in self.evidence_by_value.items()
        }
        return CustomerFacts(
            values=values,
            evidence=evidence,
            evidence_by_value=by_value,
            computed_at=self.computed_at,
            restricted_ids=set(),
            withheld_facts=withheld,
            hidden_ids=hidden,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": self.values,
            "evidence": self.evidence,
            "withheld_facts": self.withheld_facts,
            "computed_at": self.computed_at.isoformat(),
        }


# Families computed over everything by design and shown whole to every reader.
_AGGREGATE_FAMILIES = frozenset(
    {"customer", "health", "signals", "state", "activity", "memories", "request", "lifecycle", "actions"}
)


def _ids_for(index: dict[str, list[str]], kind: str, item: Any) -> list[str]:
    key = str(item).strip().lower()
    if kind == "terms":
        key = root(key)
    return index.get(key, [])


@dataclass(slots=True)
class FactInputs:
    """Everything the builder reads. Gathered by the service that owns each piece."""

    customer: Any
    now: datetime
    health: Any | None = None  # HealthScore
    report: Any | None = None  # SignalReport
    # Active memories grouped by type — see MemoryRepository.top_by_type.
    memories_by_type: dict[str, list[Any]] = field(default_factory=dict)
    goals: Sequence[Any] = ()
    distinct_features: int = 0
    memory_count: int = 0
    restricted_count: int = 0
    state: str | None = None
    state_entered_at: datetime | None = None
    state_pinned: bool = False
    # Every lifecycle track's current stay: name -> (state, entered_at).
    tracks: dict[str, tuple[str | None, datetime | None]] = field(default_factory=dict)
    # Actions agents were cleared to take or reported done, (action, when, amount), over
    # the last ACTION_HISTORY_DAYS.
    actions: Sequence[tuple[str, datetime, float | None]] = ()
    # Ids of this customer's restricted memories, so goals born from one are known too.
    restricted_memory_ids: frozenset[str] = frozenset()


def _days_since(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return round(max(0.0, (now - ensure_utc(moment)).total_seconds() / 86400), 2)


def _words(texts: Iterable[str]) -> list[str]:
    """The words a terms fact holds: as written, so the fact document stays readable.

    Matching happens on each word's :func:`root`, at evaluation time — see
    ``conditions._contains`` — so storing the surface form costs nothing in recall.
    """
    found: dict[str, None] = {}
    for text in texts:
        for word in surface_words(text):
            found[word] = None
    return sorted(found)


def _index_roots(items: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """root -> ids, from (id, text) pairs, for citing exactly what matched."""
    index: dict[str, list[str]] = {}
    for ident, text in items:
        for key in {root(word) for word in surface_words(text)}:
            index.setdefault(key, []).append(ident)
    return index


def _mapping(item: Any) -> dict[str, Any]:
    """The JSON metadata on a model or a plain object.

    Checked by type rather than truthiness: on an ORM model ``.metadata`` is SQLAlchemy's
    declarative ``MetaData``, so falling back to it whenever ``.meta`` is an empty dict
    would hand the builder the schema instead of the customer's fields.
    """
    for name in ("meta", "metadata"):
        value = getattr(item, name, None)
        if isinstance(value, dict):
            return value
    return {}


def _meta(memory: Any) -> dict[str, Any]:
    return _mapping(memory)


def _plan_from(text: str) -> str | None:
    """The plan a subscription statement ends on — the last plan it names."""
    tokens = tokenize(text)
    named = [PLANS[token] for token in tokens if token in PLANS]
    return named[-1].lower() if named else None


def _plans_from(text: str) -> list[str]:
    return [PLANS[token].lower() for token in tokenize(text) if token in PLANS]


# Only a *completed* change is a direction. "The customer cancelled" is a churn; "the
# customer will cancel unless the sync is fixed" is a threat — an intent, recorded by
# ``intents.kinds`` — and reading it as a cancellation would mark a recoverable customer
# as churned. So directions come from past-tense forms, and any intent marker vetoes.
_COMPLETED: tuple[tuple[str, frozenset[str]], ...] = (
    ("cancelled", frozenset({"cancelled", "canceled", "churned", "terminated"})),
    ("downgraded", frozenset({"downgraded"})),
    ("upgraded", frozenset({"upgraded"})),
    ("renewed", frozenset({"renewed"})),
    ("started", frozenset({"started", "subscribed", "joined", "signed", "converted"})),
)
_INTENT_MARKERS = frozenset(
    {"will", "going", "want", "wants", "wanted", "planning", "considering", "consider",
     "thinking", "might", "unless", "if", "threatened", "threatens", "threaten", "would",
     "could", "should", "intend", "intends"}
)
# Words that are markers only in a phrase: "plan" is usually the Pro *plan* — "from the
# Starter plan to the Pro plan" must not read as intent — and "may" is as often the month.
# Memories are rewritten to the third person, so "we plan to" arrives as "plans to".
_INTENT_PHRASES = frozenset({("plans", "to"), ("about", "to"), ("may", "cancel")})


_DONE = frozenset(direction for direction, _ in _COMPLETED)


def _direction_from(text: str) -> str | None:
    tokens = tokenize(text)
    if _INTENT_MARKERS & set(tokens) or _INTENT_PHRASES & set(zip(tokens, tokens[1:], strict=False)):
        return None
    words = set(tokens)
    for direction, forms in _COMPLETED:
        if words & forms:
            return direction
    return "changed"


def _entities_of(memory: Any) -> list[str]:
    names = _meta(memory).get("entity_names") or []
    return sorted({str(name).strip().lower() for name in names if str(name).strip()})


def _age_days(memory: Any, now: datetime) -> float | None:
    """Days since a memory was first seen — when the customer first said it."""
    return _days_since(getattr(memory, "first_seen_at", None), now)


def polarity(memory: Any) -> float:
    """The sentiment recorded on a memory when it was extracted, -1..1; 0 when none was."""
    sentiment = _meta(memory).get("sentiment") or {}
    try:
        return float(sentiment.get("polarity", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _action_facts(values: dict[str, Any], history: Sequence[tuple[str, datetime, float | None]], now: datetime) -> None:
    """Counts, amounts and recency per action and per family, from what agents did."""
    from memory_engine.actions import families_of  # no import cycle: actions imports nothing

    groups: dict[str, list[tuple[float, float | None]]] = {}
    for action, at, amount in history:
        age = _days_since(at, now)
        if age is None:
            continue
        for key in (action, *families_of(action)):
            groups.setdefault(key, []).append((age, amount))
    for key, rows in sorted(groups.items()):
        values[f"{ACTIONS_PREFIX}{key}.count_7d"] = sum(1 for age, _ in rows if age <= 7)
        values[f"{ACTIONS_PREFIX}{key}.count_30d"] = sum(1 for age, _ in rows if age <= 30)
        values[f"{ACTIONS_PREFIX}{key}.amount_30d"] = round(
            sum(float(amount) for age, amount in rows if age <= 30 and amount is not None), 2
        )
        values[f"{ACTIONS_PREFIX}{key}.days_since_last"] = round(min(age for age, _ in rows), 2)


def _is_negative(memory: Any) -> bool:
    return polarity(memory) < -0.1


is_negative = _is_negative
plans_named = _plans_from


def plan_of(memory: Any) -> tuple[str | None, str | None]:
    """The plan a subscription memory leaves the customer on, and the direction of the
    change it records — read exactly as ``subscription.plan`` and ``.direction`` are."""
    meta = _meta(memory)
    plan = meta.get("plan") or _plan_from(memory.content)
    direction = meta.get("direction") or _direction_from(memory.content)
    return (str(plan).lower() if plan else None, str(direction) if direction else None)


def build_facts(inputs: FactInputs) -> CustomerFacts:
    """Compute every fact in :data:`CATALOG` from what the engine already knows."""
    now = inputs.now
    customer = inputs.customer
    facts = CustomerFacts(computed_at=now)
    restricted = set(inputs.restricted_memory_ids)
    for memories in inputs.memories_by_type.values():
        for memory in memories:
            if str(getattr(memory, "sensitivity", "normal")) == "restricted":
                restricted.add(memory.id)
    for goal in inputs.goals:
        if getattr(goal, "memory_id", None) in restricted:
            restricted.add(goal.id)
    facts.restricted_ids = restricted
    values = facts.values
    evidence = facts.evidence
    by_value = facts.evidence_by_value

    # ------------------------------------------------------------------ who
    email = getattr(customer, "email", None)
    values["customer.external_id"] = getattr(customer, "external_id", None)
    values["customer.name"] = getattr(customer, "name", None)
    values["customer.email"] = email
    values["customer.email_domain"] = email.split("@", 1)[1].lower() if email and "@" in email else None
    values["customer.age_days"] = _days_since(getattr(customer, "created_at", None), now)
    values["customer.metadata"] = dict(_mapping(customer))

    # --------------------------------------------------------------- health
    health = inputs.health
    if health is not None:
        values["health.score"] = round(float(health.score), 1)
        values["health.band"] = str(health.band)
        values["health.churn_risk"] = round(float(health.churn_risk), 3)
        ids = [ident for factor in health.factors for ident in factor.memory_ids]
        for name in ("health.score", "health.band", "health.churn_risk"):
            evidence[name] = list(dict.fromkeys(ids))[:10]

    # -------------------------------------------------------------- signals
    report = inputs.report
    if report is not None:
        values["signals.trajectory"] = str(report.trajectory)
        values["signals.churn_risk"] = round(float(report.churn_risk), 3)
        values["signals.expansion_score"] = round(float(report.expansion_score), 3)
        values["signals.confidence"] = round(float(report.confidence), 3)
        values["signals.risk_pressure"] = round(float(report.measurements.get("risk_pressure", 0.0)), 3)
        values["signals.active"] = [signal.key for signal in report.signals]
        values["signals.risks"] = [signal.key for signal in report.risks]
        values["signals.opportunities"] = [signal.key for signal in report.opportunities]
        by_value["signals.active"] = {signal.key: list(signal.memory_ids) for signal in report.signals}
        by_value["signals.risks"] = {signal.key: list(signal.memory_ids) for signal in report.risks}
        by_value["signals.opportunities"] = {
            signal.key: list(signal.memory_ids) for signal in report.opportunities
        }
        measurements = report.measurements
        values["activity.events_recent"] = int(measurements.get("events_recent", 0))
        values["activity.events_prior"] = int(measurements.get("events_prior", 0))
        values["problems.recent_count"] = int(measurements.get("problems_recent", 0))

    # ---------------------------------------------------------------- state
    values["state.current"] = inputs.state
    values["state.days_in_state"] = _days_since(inputs.state_entered_at, now)
    values["state.pinned"] = bool(inputs.state_pinned)
    for track, (track_state, entered_at) in inputs.tracks.items():
        values[f"{LIFECYCLE_PREFIX}{track}"] = track_state
        values[f"{LIFECYCLE_PREFIX}{track}{DAYS_SUFFIX}"] = _days_since(entered_at, now)

    _action_facts(values, inputs.actions, now)

    grouped = inputs.memories_by_type

    # --------------------------------------------------------- subscription
    subscriptions = grouped.get("subscription", [])
    if subscriptions:
        # A plan is a state: the newest statement of it is the true one (§12b).
        current = max(subscriptions, key=lambda memory: ensure_utc(memory.last_seen_at))
        meta = _meta(current)
        plan = meta.get("plan") or _plan_from(current.content)
        previous = meta.get("previous_plan")
        if previous is None:
            named = _plans_from(current.content)
            previous = named[0] if len(named) >= 2 else None
        direction = meta.get("direction") or _direction_from(current.content)
        values["subscription.plan"] = str(plan).lower() if plan else None
        values["subscription.previous_plan"] = str(previous).lower() if previous else None
        values["subscription.direction"] = str(direction) if direction else None
        values["subscription.changed_days_ago"] = _days_since(current.last_seen_at, now)
        for name in ("subscription.plan", "subscription.previous_plan", "subscription.direction", "subscription.changed_days_ago"):
            evidence[name] = [current.id]
    else:
        values["subscription.plan"] = None
        values["subscription.previous_plan"] = None
        values["subscription.direction"] = None
        values["subscription.changed_days_ago"] = None

    # ------------------------------------------------------------- problems
    problems = grouped.get("problem", [])
    problem_ids = [memory.id for memory in problems]
    values["problems.open_count"] = len(problems)
    entity_index: dict[str, list[str]] = {}
    for memory in problems:
        for entity in _entities_of(memory):
            entity_index.setdefault(entity, []).append(memory.id)
    values["problems.entities"] = sorted(entity_index)
    by_value["problems.entities"] = entity_index
    values["problems.terms"] = _words(memory.content for memory in problems)
    by_value["problems.terms"] = _index_roots((memory.id, memory.content) for memory in problems)
    oldest = min((ensure_utc(memory.first_seen_at) for memory in problems), default=None)
    values["problems.oldest_open_days"] = _days_since(oldest, now)
    values["problems.max_repeats"] = max((int(memory.evidence_count or 1) for memory in problems), default=0)
    for name in ("problems.open_count", "problems.oldest_open_days", "problems.max_repeats", "problems.entities", "problems.terms"):
        evidence[name] = problem_ids[:10]

    # ---------------------------------------------------------------- goals
    counts = dict.fromkeys(("open", "progressing", "stalled", "achieved", "abandoned"), 0)
    goal_ids: dict[str, list[str]] = {status: [] for status in counts}
    live_goals: list[Any] = []
    for goal in inputs.goals:
        status = str(goal.status)
        if status in counts:
            counts[status] += 1
            goal_ids[status].append(goal.id)
        if status in ("open", "progressing"):
            live_goals.append(goal)
    for status, count in counts.items():
        values[f"goals.{status}_count"] = count
        evidence[f"goals.{status}_count"] = goal_ids[status][:10]
    values["goals.terms"] = _words(goal.statement for goal in live_goals)
    by_value["goals.terms"] = _index_roots((goal.id, goal.statement) for goal in live_goals)

    # ---------------------------------------------------------- preferences
    preferences = grouped.get("preference", [])
    newest_first = sorted(preferences, key=lambda memory: ensure_utc(memory.last_seen_at), reverse=True)
    channel_index: dict[str, list[str]] = {}
    preferred: str | None = None
    preferred_id: str | None = None
    # Read for stance, not just mention: "WhatsApp instead of email" prefers WhatsApp, and a
    # newer "stop emailing us" rules email out even where an older preference named it.
    turned_away: set[str] = set()
    for memory in newest_first:
        wanted, avoided = channel_stance(memory.content)
        if not wanted and not avoided:
            wanted = [str(channel) for channel in _meta(memory).get("channels") or []]
        for channel in [*wanted, *avoided]:
            channel_index.setdefault(str(channel).lower(), []).append(memory.id)
        if preferred is None:
            choice = next((str(channel).lower() for channel in wanted if str(channel).lower() not in turned_away), None)
            if choice is not None:
                preferred, preferred_id = choice, memory.id
        turned_away.update(str(channel).lower() for channel in avoided)
    values["preferences.channel"] = preferred
    values["preferences.channels"] = sorted(channel_index)
    # Opt-outs live in preferences and in feedback ("stop calling us" is often a complaint).
    opt_out_index: dict[str, list[str]] = {}
    for memory in [*newest_first, *grouped.get("feedback", [])]:
        for kind in opt_outs(memory.content):
            opt_out_index.setdefault(kind, []).append(memory.id)
    values["preferences.opt_outs"] = [kind for kind in OPT_OUT_KINDS if kind in opt_out_index]
    by_value["preferences.opt_outs"] = opt_out_index
    evidence["preferences.opt_outs"] = list(dict.fromkeys(i for ids in opt_out_index.values() for i in ids))[:10]
    values["preferences.terms"] = _words(memory.content for memory in preferences)
    evidence["preferences.channel"] = [preferred_id] if preferred_id else []
    by_value["preferences.channels"] = channel_index
    by_value["preferences.terms"] = _index_roots((memory.id, memory.content) for memory in preferences)

    # -------------------------------------------------------------- intents
    intents = grouped.get("intent", [])
    kind_index: dict[str, list[str]] = {}
    latest_kind: str | None = None
    latest_at: datetime | None = None
    for memory in sorted(intents, key=lambda item: ensure_utc(item.last_seen_at), reverse=True):
        kinds = intent_kinds(memory.content)
        for kind in kinds:
            kind_index.setdefault(kind, []).append(memory.id)
        if kinds and latest_kind is None:
            latest_kind, latest_at = kinds[0], memory.last_seen_at
    # Churn language on a problem, feedback or subscription memory is intent too — "we
    # will cancel if this is not fixed" is filed under whichever type the extractor chose,
    # and a rule asking whether the customer intends to cancel must not miss it for that.
    # Only the high-stakes kinds: an "integration" intent read off every Shopify problem
    # would make the kind meaningless.
    # A subscription statement recording a change that *happened* ("downgraded from Pro to
    # Starter") is history, read by subscription.direction — not something they intend.
    happened = {memory.id for memory in subscriptions if plan_of(memory)[1] in _DONE}
    for memory in [*grouped.get("problem", []), *grouped.get("feedback", []), *subscriptions]:
        if memory.id in happened:
            continue
        for kind in ("cancellation", "downgrade"):
            if kind in intent_kinds(memory.content) and memory.id not in kind_index.get(kind, []):
                kind_index.setdefault(kind, []).append(memory.id)
    values["intents.kinds"] = [kind for kind in INTENT_KINDS if kind in kind_index]
    values["intents.latest_kind"] = latest_kind
    values["intents.latest_days_ago"] = _days_since(latest_at, now)
    by_value["intents.kinds"] = kind_index
    evidence["intents.kinds"] = list(dict.fromkeys(ident for ids in kind_index.values() for ident in ids))[:10]

    # ------------------------------------------------------------- activity
    values["activity.last_event_days_ago"] = _days_since(getattr(customer, "last_event_at", None), now)
    values["activity.distinct_features"] = int(inputs.distinct_features)
    recent = values.get("activity.events_recent")
    prior = values.get("activity.events_prior")
    values["activity.trend"] = _trend(recent, prior, values["activity.last_event_days_ago"])
    # A percentage of nothing is not a number: with no prior activity the change is unknown.
    values["activity.change_pct"] = (
        round((int(recent or 0) - int(prior)) / int(prior) * 100, 1) if prior else None
    )

    # ------------------------------------------------------------- feedback
    feedback = grouped.get("feedback", [])
    negative = [memory for memory in feedback if _is_negative(memory)]
    values["feedback.count"] = len(feedback)
    values["feedback.negative_count"] = len(negative)
    evidence["feedback.negative_count"] = [memory.id for memory in negative][:10]
    recent_negative = [m for m in negative if _age_days(m, now) is not None and _age_days(m, now) <= RECENT_DAYS]
    prior_negative = [
        m for m in negative if _age_days(m, now) is not None and RECENT_DAYS < _age_days(m, now) <= 2 * RECENT_DAYS
    ]
    values["feedback.recent_negative_count"] = len(recent_negative)
    values["feedback.prior_negative_count"] = len(prior_negative)
    values["feedback.negative_trend"] = (
        "rising"
        if len(recent_negative) > len(prior_negative) and len(recent_negative) >= 2
        else "falling"
        if len(recent_negative) < len(prior_negative)
        else "steady"
    )
    evidence["feedback.recent_negative_count"] = [memory.id for memory in recent_negative][:10]
    evidence["feedback.negative_trend"] = [memory.id for memory in recent_negative][:10]

    # ------------------------------------------------------------- memories
    values["memories.count"] = int(inputs.memory_count)
    values["memories.restricted_count"] = int(inputs.restricted_count)

    return facts


def _trend(recent: int | None, prior: int | None, idle_days: float | None) -> str | None:
    """Growing, steady, declining or silent — from the same windows the forecast uses."""
    if recent is None or prior is None:
        return None
    if recent == 0 and (idle_days is None or idle_days >= 14):
        return "silent"
    if prior == 0:
        return "growing" if recent > 0 else "silent"
    ratio = recent / prior
    if ratio >= 1.25:
        return "growing"
    if ratio <= 0.75:
        return "declining"
    return "steady"
