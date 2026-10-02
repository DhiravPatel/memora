"""What a product should do differently for this customer (§26 6.6).

Memora remembers customers; personalization lets the product *adapt* to them. One call
answers, deterministically and with the facts behind every answer:

* **experience** — how experienced with the product they are (new, beginner, intermediate,
  advanced), from age and breadth of use;
* **mood** — how they seem right now (frustrated, happy, neutral);
* **preferred channel** and **opt-outs**;
* **current goal** — what they are working towards;
* **known frictions** — what is getting in their way, as stable keys a product can target:
  the product, integration or feature a problem names (``shopify``), or else the area it is
  about (``billing``, ``login``, ``data_export``);
* **features used**, and the ones they rely on;
* **stage**, **plan** and **health**;
* **UI hints** — ``show_onboarding``, ``show_advanced_features``, ``suppress_upsell``,
  ``suppress_marketing``, ``offer_help``, ``ask_for_review`` and the project's own — each a
  condition in the condition language, over the fact document plus
  ``personalization.experience``, ``personalization.mood`` and ``guardrail.<action>``: what
  the guardrails would say to that action now, so a product never shows the upsell an
  agent would be refused.

Experience levels, moods and hints are rules a project can change; the defaults below are a
reasonable SaaS starting point. Personalization reads no restricted memory: it drives a
product's UI, and nothing in it may quote what a restriction policy protects.

Everything here is pure. The service gathers the inputs, stores the result per customer and
tells webhook subscribers when it changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from common.time import ensure_utc
from memory_engine.conditions import Condition, ConditionError, compile_condition
from memory_engine.facts import GUARDRAIL_PREFIX, LIFECYCLE_PREFIX, CustomerFacts
from memory_engine.reasons import reasons as reasons_in_words
from memory_engine.topics import topics_of
from nlp.concepts import concepts_for
from nlp.concepts import label as concept_label

# ------------------------------------------------------------------ the rules

EXPERIENCE_DEFAULTS: tuple[dict[str, Any], ...] = (
    {"level": "new", "when": "customer.age_days < 14 and activity.distinct_features < 3"},
    {"level": "advanced", "when": 'activity.distinct_features >= 5 or lifecycle.engagement == "power_user"'},
    {"level": "intermediate", "when": "activity.distinct_features >= 3"},
    {"level": "beginner", "when": None},
)
MOOD_DEFAULTS: tuple[dict[str, Any], ...] = (
    {
        "mood": "frustrated",
        "when": 'feedback.recent_negative_count >= 1 or intents.kinds contains "cancellation" or problems.max_repeats >= 3',
    },
    {"mood": "happy", "when": 'signals.opportunities contains "advocacy"'},
    {"mood": "neutral", "when": None},
)
HINT_DEFAULTS: dict[str, dict[str, Any]] = {
    "show_onboarding": {
        "when": 'personalization.experience in ["new", "beginner"]',
        "description": "They are new to the product or use little of it: guide them.",
    },
    "show_advanced_features": {
        "when": 'personalization.experience == "advanced"',
        "description": "They use most of the product: surface the power features.",
    },
    "suppress_upsell": {
        "when": 'guardrail.offer_upgrade != "allow"',
        "description": "The guardrails would not let an agent offer an upgrade now — don't show one either.",
    },
    "suppress_marketing": {
        "when": 'guardrail.send_marketing != "allow"',
        "description": "They opted out of marketing, or the guardrails would refuse it.",
    },
    "offer_help": {
        "when": "problems.open_count >= 1 and problems.recent_count >= 1",
        "description": "They reported a problem recently that is still open: offer help where they work.",
    },
    "ask_for_review": {
        "when": 'guardrail.request_review == "allow" and personalization.mood == "happy"',
        "description": "Happy and unblocked, and the guardrails allow asking: a good moment for a review.",
    },
}
RELIED_ON_USES = 3
RELIED_ON_DAYS = 30
MAX_RULES = 10
MAX_CUSTOM_HINTS = 20
MAX_DESCRIPTION = 300
HINT_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
LEVEL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_FRICTIONS = 10
MAX_FEATURES = 20
MAX_EVIDENCE = 25

# A friction's area, when it names no product: the most specific concept it is about.
# "Something is broken" and "something is slow" are how it fails, not what it is about.
AREAS = (
    "billing", "data_loss", "login", "security", "data_export", "integration", "onboarding",
    "reporting", "notifications", "mobile", "orders", "shipping", "users", "usability", "support",
    "contact",
)
MODES = (("broken", "broken"), ("slow", "slow"))


class PersonalizationError(ValueError):
    """Rules that cannot be compiled — refused when saved, never at evaluation."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One ordered rule: ``value`` when ``condition`` holds (or always, with none)."""

    value: str
    condition: Condition | None


@dataclass(frozen=True, slots=True)
class Hint:
    key: str
    condition: Condition
    description: str
    custom: bool


@dataclass(frozen=True, slots=True)
class Rules:
    experience: tuple[Rule, ...]
    mood: tuple[Rule, ...]
    hints: tuple[Hint, ...]
    relied_on_uses: int = RELIED_ON_USES
    relied_on_days: int = RELIED_ON_DAYS

    def guardrail_actions(self) -> list[str]:
        """The actions whose guardrail verdict any rule reads — the only ones worth judging."""
        found: list[str] = []
        conditions = [hint.condition for hint in self.hints]
        conditions += [rule.condition for rule in (*self.experience, *self.mood) if rule.condition is not None]
        for condition in conditions:
            for fact in condition.facts:
                if fact.startswith(GUARDRAIL_PREFIX) and fact[len(GUARDRAIL_PREFIX) :] not in found:
                    found.append(fact[len(GUARDRAIL_PREFIX) :])
        return found


def _compile(text: Any, where: str) -> Condition | None:
    if text is None or (isinstance(text, str) and not text.strip()):
        return None
    try:
        return compile_condition(text)
    except ConditionError as exc:
        raise PersonalizationError(f"{where}: {exc}") from exc


def _ordered(raw: Any, defaults: Sequence[dict[str, Any]], name: str) -> tuple[Rule, ...]:
    entries = defaults if raw is None else raw
    if not isinstance(entries, list | tuple) or not entries:
        raise PersonalizationError(f"'{name}' is a list of {{\"{name_key(name)}\": …, \"when\": …}} rules, tried in order.")
    if len(entries) > MAX_RULES:
        raise PersonalizationError(f"At most {MAX_RULES} {name} rules.")
    rules: list[Rule] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PersonalizationError(f"{name} rule {position + 1} must be an object.")
        value = str(entry.get(name_key(name)) or "").strip().lower()
        if not LEVEL_NAME.match(value):
            raise PersonalizationError(
                f"{name} rule {position + 1}: {value!r} is not a valid name — lowercase letters, digits and '_'."
            )
        if value in seen:
            raise PersonalizationError(f"{name} {value!r} is listed twice.")
        seen.add(value)
        condition = _compile(entry.get("when"), f"{name} {value!r}")
        if condition is None and position != len(entries) - 1:
            raise PersonalizationError(f"{name} {value!r} has no condition, so only the last rule may: it is the fallback.")
        rules.append(Rule(value, condition))
    return tuple(rules)


def name_key(name: str) -> str:
    return "level" if name == "experience" else "mood"


def compile_rules(raw: dict[str, Any] | None) -> Rules:
    """Validate a project's personalization rules over the defaults. ``None`` or ``{}`` is
    the defaults."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise PersonalizationError("Personalization must be an object.")
    experience = _ordered(raw.get("experience"), EXPERIENCE_DEFAULTS, "experience")
    mood = _ordered(raw.get("mood"), MOOD_DEFAULTS, "mood")

    overrides = raw.get("hints") or {}
    if not isinstance(overrides, dict):
        raise PersonalizationError("'hints' is an object of hint name → {\"when\", \"description\", \"enabled\"}.")
    custom = [key for key in overrides if key not in HINT_DEFAULTS]
    if len(custom) > MAX_CUSTOM_HINTS:
        raise PersonalizationError(f"At most {MAX_CUSTOM_HINTS} hints of your own.")
    hints: list[Hint] = []
    for key in [*HINT_DEFAULTS, *custom]:
        if not HINT_KEY.match(str(key)):
            raise PersonalizationError(f"{key!r} is not a valid hint name — lowercase letters, digits and '_', 2–40 long.")
        base = HINT_DEFAULTS.get(key, {})
        override = overrides.get(key) or {}
        if not isinstance(override, dict):
            raise PersonalizationError(f"Hint {key!r} must be an object.")
        if override.get("enabled") is False:
            continue
        when = override.get("when", base.get("when"))
        condition = _compile(when, f"Hint {key!r}")
        if condition is None:
            raise PersonalizationError(f"Hint {key!r} needs a condition ('when').")
        description = str(override.get("description") or base.get("description") or "").strip()
        if len(description) > MAX_DESCRIPTION:
            raise PersonalizationError(f"Hint {key!r}: the description is longer than {MAX_DESCRIPTION} characters.")
        hints.append(Hint(str(key), condition, description, key not in HINT_DEFAULTS))

    uses = raw.get("relied_on_uses", RELIED_ON_USES)
    days = raw.get("relied_on_days", RELIED_ON_DAYS)
    if isinstance(uses, bool) or not isinstance(uses, int) or not 1 <= uses <= 1000:
        raise PersonalizationError("'relied_on_uses' is a whole number from 1 to 1000.")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise PersonalizationError("'relied_on_days' is a whole number of days from 1 to 365.")
    return Rules(tuple(experience), tuple(mood), tuple(hints), uses, days)


def canonical(raw: dict[str, Any] | None) -> dict[str, Any]:
    """What is stored: only what differs from the defaults, so a project picks up better
    defaults without having to touch its settings."""
    compile_rules(raw)  # refuses anything that would not compile
    raw = dict(raw or {})
    stored: dict[str, Any] = {}
    for name, defaults in (("experience", EXPERIENCE_DEFAULTS), ("mood", MOOD_DEFAULTS)):
        entries = raw.get(name)
        if entries is not None:
            cleaned = [
                {name_key(name): str(entry[name_key(name)]).strip().lower(), "when": entry.get("when") or None}
                for entry in entries
            ]
            if cleaned != [dict(item) for item in defaults]:
                stored[name] = cleaned
    hints: dict[str, Any] = {}
    for key, override in (raw.get("hints") or {}).items():
        base = HINT_DEFAULTS.get(key)
        entry = {name: override[name] for name in ("when", "description", "enabled") if name in override}
        if base is not None:
            entry = {name: value for name, value in entry.items() if base.get(name, True if name == "enabled" else None) != value}
        if entry or base is None:
            hints[key] = entry
    if hints:
        stored["hints"] = hints
    for name, default in (("relied_on_uses", RELIED_ON_USES), ("relied_on_days", RELIED_ON_DAYS)):
        if name in raw and raw[name] != default:
            stored[name] = raw[name]
    return stored


def describe(rules: Rules) -> dict[str, Any]:
    """The rules in force, defaults included — for the settings form and the docs."""
    return {
        "experience": [{"level": rule.value, "when": rule.condition.text if rule.condition else None} for rule in rules.experience],
        "mood": [{"mood": rule.value, "when": rule.condition.text if rule.condition else None} for rule in rules.mood],
        "hints": [
            {"key": hint.key, "when": hint.condition.text, "description": hint.description, "custom": hint.custom}
            for hint in rules.hints
        ],
        "relied_on_uses": rules.relied_on_uses,
        "relied_on_days": rules.relied_on_days,
        "builtin_hints": list(HINT_DEFAULTS),
        "defaults": {
            "experience": [dict(item) for item in EXPERIENCE_DEFAULTS],
            "mood": [dict(item) for item in MOOD_DEFAULTS],
            "hints": {key: dict(value) for key, value in HINT_DEFAULTS.items()},
            "relied_on_uses": RELIED_ON_USES,
            "relied_on_days": RELIED_ON_DAYS,
        },
    }


# ------------------------------------------------------------------ inputs


@dataclass(slots=True)
class Usage:
    """A feature or integration the customer used."""

    kind: str  # feature | integration
    name: str
    first_at: datetime
    last_at: datetime
    uses: int
    recent: int = 0
    event_id: str | None = None


@dataclass(slots=True)
class PersonalizationInputs:
    now: datetime
    # The fact document with restricted content withheld and numbers whole.
    facts: CustomerFacts
    rules: Rules
    # action -> {"decision": allow | require_approval | deny, "summary": "…"}
    guardrails: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Open problems and live goals this document may name — none restricted.
    problems: Sequence[Any] = ()
    goals: Sequence[Any] = ()
    uses: Sequence[Usage] = ()
    topic_types: dict[str, str] = field(default_factory=dict)
    customer_name: str = ""


# ------------------------------------------------------------------ compute


def slug(text: Any, limit: int = 40) -> str:
    words = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return words[:limit].rstrip("_")


def _first(rules: Sequence[Rule], facts: CustomerFacts) -> tuple[str | None, list[str], str | None]:
    """The first rule that holds: its value, the reasons in words, and the rule."""
    for rule in rules:
        if rule.condition is None:
            return rule.value, [], None
        evaluation = rule.condition.evaluate(facts)
        if evaluation.matched:
            return rule.value, reasons_in_words(evaluation), rule.condition.text
    return None, [], None


def _frictions(inputs: PersonalizationInputs) -> list[dict[str, Any]]:
    """Open problems as stable keys: the product, integration or feature they name, or else
    the area they are about — with how they fail, how often, and since when."""
    grouped: dict[str, dict[str, Any]] = {}
    for memory in inputs.problems:
        meta = getattr(memory, "meta", None) or {}
        if meta.get("resolved"):
            continue
        concepts = concepts_for(memory.content)
        topics = topics_of(memory, inputs.topic_types, exclude=inputs.customer_name, limit=1)
        area = next((concept for concept in AREAS if concept in concepts), None)
        if topics:
            key, name = slug(topics[0]), topics[0]
        elif area:
            key, name = area, concept_label(area)
        else:
            key, name = "other", "Something else"
        mode = next((mode for concept, mode in MODES if concept in concepts), "other")
        entry = grouped.setdefault(
            key,
            {
                "key": key,
                "label": name,
                "area": area,
                "mode": mode,
                "problems": 0,
                "reports": 0,
                "since": ensure_utc(memory.first_seen_at),
                "last_reported_at": ensure_utc(memory.last_seen_at),
                "memory_ids": [],
            },
        )
        entry["problems"] += 1
        entry["reports"] += int(getattr(memory, "evidence_count", 1) or 1)
        entry["since"] = min(entry["since"], ensure_utc(memory.first_seen_at))
        entry["last_reported_at"] = max(entry["last_reported_at"], ensure_utc(memory.last_seen_at))
        if entry["mode"] == "other" and mode != "other":
            entry["mode"] = mode
        entry["memory_ids"].append(memory.id)
    found = sorted(grouped.values(), key=lambda item: (-item["reports"], -item["last_reported_at"].timestamp()))
    return found[:MAX_FRICTIONS]


_GOAL_ORDER = {"progressing": 0, "open": 1, "stalled": 2}


def _goal(inputs: PersonalizationInputs) -> dict[str, Any] | None:
    """What they are working towards: progressing first, then open, then stalled; the most
    recently moved of those."""
    from memory_engine.journey import goal_words  # the journey names goals the same way

    live = [goal for goal in inputs.goals if str(getattr(goal, "status", "")) in _GOAL_ORDER]
    if not live:
        return None
    goal = min(
        live,
        key=lambda item: (_GOAL_ORDER[str(item.status)], -ensure_utc(item.last_signal_at).timestamp()),
    )
    label = goal_words(str(goal.statement or ""))
    return {
        "id": goal.id,
        "key": slug(label),
        "label": label,
        "status": str(goal.status),
        "progress": round(float(getattr(goal, "progress", 0.0) or 0.0), 2),
        "memory_id": getattr(goal, "memory_id", None),
    }


def _features(inputs: PersonalizationInputs) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for use in inputs.uses:
        key = slug(use.name)
        if not key or key in found:
            continue
        found[key] = {
            "key": key,
            "name": use.name,
            "kind": use.kind,
            "uses": int(use.uses),
            "recent_uses": int(use.recent),
            "first_used_at": ensure_utc(use.first_at),
            "last_used_at": ensure_utc(use.last_at),
            "relied_on": int(use.recent) >= inputs.rules.relied_on_uses,
        }
    ordered = sorted(found.values(), key=lambda item: (-item["recent_uses"], -item["last_used_at"].timestamp()))
    return ordered[:MAX_FEATURES]


def _hint_because(hint: Hint, evaluation: Any, guardrails: dict[str, dict[str, Any]]) -> list[str]:
    """Why a hint is what it is: a guardrail's own sentence where it decided, otherwise the
    decisive clauses in words."""
    because: list[str] = []
    for leaf in getattr(evaluation, "decisive", []) or []:
        fact = str(leaf.fact)
        if fact.startswith(GUARDRAIL_PREFIX):
            verdict = guardrails.get(fact[len(GUARDRAIL_PREFIX) :]) or {}
            text = verdict.get("summary") if verdict.get("decision") not in (None, "allow") else None
            text = text or reasons_in_words({"decisive": [{"fact": fact, "actual": leaf.actual}]})
            if isinstance(text, list):
                because.extend(text)
            elif text:
                because.append(text)
        else:
            because.extend(reasons_in_words({"decisive": [{"fact": fact, "actual": leaf.actual}]}))
    return list(dict.fromkeys(item for item in because if item))[:4]


def compute(inputs: PersonalizationInputs) -> dict[str, Any]:
    """The personalization document, before the service stamps it with an id and a time."""
    facts = inputs.facts
    rules = inputs.rules
    experience, experience_because, experience_rule = _first(rules.experience, facts)
    mood, mood_because, mood_rule = _first(rules.mood, facts)

    # The hints read the document plus what was just decided and what the guardrails say.
    extended = replace(facts, values=dict(facts.values))
    extended.values["personalization.experience"] = experience
    extended.values["personalization.mood"] = mood
    for action, verdict in inputs.guardrails.items():
        extended.values[f"{GUARDRAIL_PREFIX}{action}"] = verdict.get("decision")
    ui: dict[str, bool] = {}
    hint_details: dict[str, dict[str, Any]] = {}
    for hint in rules.hints:
        evaluation = hint.condition.evaluate(extended)
        value = evaluation.outcome is True
        ui[hint.key] = value
        hint_details[hint.key] = {
            "value": value,
            "known": evaluation.outcome is not None,
            "because": _hint_because(hint, evaluation, inputs.guardrails),
            "rule": hint.condition.text,
            "description": hint.description,
            "custom": hint.custom,
        }

    frictions = _frictions(inputs)
    goal = _goal(inputs)
    features = _features(inputs)
    stage = {"lifecycle": facts.get("state.current")} if facts.get("state.current") is not None else {}
    for name, value in sorted(facts.values.items()):
        if name.startswith(LIFECYCLE_PREFIX) and not name.endswith(".days_in_state") and value is not None:
            stage[name[len(LIFECYCLE_PREFIX) :]] = value
    channel = facts.get("preferences.channel")
    health_score = facts.get("health.score")

    evidence: list[str] = []
    for item in frictions:
        evidence.extend(item["memory_ids"])
    if goal is not None:
        evidence.append(goal["id"])
    evidence.extend(facts.evidence.get("preferences.channel", []))
    evidence.extend(facts.evidence.get("preferences.opt_outs", []))

    return {
        "experience": experience,
        "mood": mood,
        "preferred_channel": str(channel).lower() if channel else None,
        "opt_outs": list(facts.get("preferences.opt_outs") or []),
        "current_goal": goal["key"] if goal else None,
        "known_frictions": [item["key"] for item in frictions],
        "features_used": [item["key"] for item in features],
        "relied_on_features": [item["key"] for item in features if item["relied_on"]],
        "stage": stage.get("lifecycle"),
        "plan": facts.get("subscription.plan"),
        "health": facts.get("health.band"),
        "ui": ui,
        "evidence": list(dict.fromkeys(evidence))[:MAX_EVIDENCE],
        "details": {
            "experience": {"value": experience, "because": experience_because, "rule": experience_rule},
            "mood": {"value": mood, "because": mood_because, "rule": mood_rule},
            "preferred_channel": {
                "value": str(channel).lower() if channel else None,
                "outdated": facts.get("preferences.channel_outdated"),
                "observed": facts.get("preferences.observed_channel"),
                "opt_outs": list(facts.get("preferences.opt_outs") or []),
            },
            "current_goal": goal,
            "known_frictions": frictions,
            "features_used": features,
            "stage": stage,
            "health": {
                "band": facts.get("health.band"),
                "score": round(float(health_score), 1) if isinstance(health_score, int | float) else None,
            },
            "ui": hint_details,
        },
    }


# ------------------------------------------------------------------ versions


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def fingerprint(document: dict[str, Any]) -> str:
    """A hash of what the product sees — not of when it was computed — so an unchanged
    customer keeps their version, and an ETag."""
    body = {key: value for key, value in document.items() if key not in ("computed_at", "version", "customer_id")}
    encoded = json.dumps(_plain(body), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:20]


FIELDS = (
    "experience", "mood", "preferred_channel", "opt_outs", "current_goal", "known_frictions",
    "features_used", "relied_on_features", "stage", "plan", "health",
)


def changes(before: dict[str, Any] | None, after: dict[str, Any]) -> list[dict[str, Any]]:
    """What moved between two documents, field by field and hint by hint — what a webhook
    receiver acts on. The first document has no before, and reports nothing."""
    if not before:
        return []
    found: list[dict[str, Any]] = []
    for name in FIELDS:
        if _plain(before.get(name)) != _plain(after.get(name)):
            found.append({"field": name, "before": before.get(name), "after": after.get(name)})
    old_ui, new_ui = before.get("ui") or {}, after.get("ui") or {}
    for key in sorted(set(old_ui) | set(new_ui)):
        if old_ui.get(key) != new_ui.get(key):
            found.append({"field": f"ui.{key}", "before": old_ui.get(key), "after": new_ui.get(key)})
    return found


def stale(computed_at: datetime | None, now: datetime, *, max_age: timedelta = timedelta(hours=26)) -> bool:
    """Older than a night's refresh plus slack: something time-based may have moved."""
    return computed_at is None or now - ensure_utc(computed_at) > max_age


__all__ = [
    "AREAS",
    "EXPERIENCE_DEFAULTS",
    "FIELDS",
    "HINT_DEFAULTS",
    "MOOD_DEFAULTS",
    "PersonalizationError",
    "PersonalizationInputs",
    "Rules",
    "Usage",
    "canonical",
    "changes",
    "compile_rules",
    "compute",
    "describe",
    "fingerprint",
    "slug",
    "stale",
]
