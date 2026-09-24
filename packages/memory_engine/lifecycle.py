"""The customer lifecycle as a deterministic state machine.

Health, signals, goals and intents are each true and none of them is a *decision*. A
lifecycle state is: "at_risk" is something a support lead can filter on, a workflow can
trigger on, and an agent can be told. This module turns the facts (§26 1.1) into one.

A machine is data — states, an initial state, and transitions whose ``when`` is written in
the condition language — so every project can define its own. Transitions are tried in the
order written and the first that matches wins, which makes precedence something you read
off the list rather than something you infer. A customer may move more than one step in
a single evaluation (a brand-new paying customer can go onboarding → active at once), up to
:data:`MAX_HOPS`, and never revisits a state within one evaluation, so a badly written pair
of rules cannot spin.

The default machine is a reasonable SaaS lifecycle with *hysteresis* — the thresholds that
move a customer into ``at_risk`` are stricter than the ones that let them leave — so a
customer hovering around a boundary does not flap between states on every event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memory_engine.conditions import Condition, ConditionError, Evaluation, compile_condition
from memory_engine.facts import CustomerFacts

MAX_STATES = 20
MAX_TRANSITIONS = 40
MAX_HOPS = 3
ANY = "*"

DEFAULT_LIFECYCLE: dict[str, Any] = {
    "states": ["trial", "onboarding", "active", "expanding", "at_risk", "churned"],
    "initial": "onboarding",
    "transitions": [
        {
            "name": "churned",
            "from": ["trial", "onboarding", "active", "expanding", "at_risk"],
            "to": "churned",
            "when": 'subscription.direction == "cancelled" or customer.metadata.status == "churned"',
        },
        {
            "name": "won_back",
            "from": ["churned"],
            "to": "active",
            "when": (
                'subscription.direction in ["started", "upgraded", "renewed"] '
                "and subscription.changed_days_ago <= 30"
            ),
        },
        {
            "name": "at_risk",
            "from": ["trial", "onboarding", "active", "expanding"],
            "to": "at_risk",
            "when": (
                'health.band in ["at_risk", "critical"] or signals.churn_risk >= 0.6 '
                'or intents.kinds contains "cancellation"'
            ),
        },
        {
            # Stricter to leave than to enter: hysteresis, so a customer near the line does
            # not flap between active and at_risk on every event.
            "name": "recovered",
            "from": ["at_risk"],
            "to": "active",
            "when": (
                'health.band in ["healthy", "watch"] and signals.churn_risk < 0.4 '
                'and intents.kinds does not contain "cancellation"'
            ),
        },
        {
            "name": "on_trial",
            "from": ["onboarding"],
            "to": "trial",
            "when": 'subscription.plan == "trial" or customer.metadata.plan == "trial"',
        },
        {
            "name": "converted",
            "from": ["trial"],
            "to": "onboarding",
            "when": 'subscription.plan is set and subscription.plan != "trial"',
        },
        {
            "name": "activated",
            "from": ["onboarding"],
            "to": "active",
            "when": (
                "activity.distinct_features >= 3 "
                "or (customer.age_days >= 30 and activity.events_recent >= 5)"
            ),
        },
        {
            "name": "expanding",
            "from": ["active"],
            "to": "expanding",
            "when": (
                'signals.expansion_score >= 0.6 or intents.kinds contains "expansion" '
                'or (subscription.direction == "upgraded" and subscription.changed_days_ago <= 30)'
            ),
        },
        {
            "name": "settled",
            "from": ["expanding"],
            "to": "active",
            "when": (
                'signals.expansion_score < 0.3 and intents.kinds does not contain "expansion" '
                "and state.days_in_state >= 30"
            ),
        },
    ],
}


# The machine above is the *primary* track: its state is `state.current`, and it is what
# every surface shows first. Projects may run more tracks beside it (§26 4.2) — one machine
# cannot say both how engaged a customer is and where they are commercially.
PRIMARY_TRACK = "lifecycle"
MAX_TRACKS = 6

ENGAGEMENT_TEMPLATE: dict[str, Any] = {
    "label": "Engagement",
    "description": "How deeply the customer uses the product, from first use to power user — and back.",
    "states": ["new", "activated", "adopting", "power_user", "at_risk", "churned"],
    "initial": "new",
    "transitions": [
        {
            "name": "churned",
            "from": ["new", "activated", "adopting", "power_user", "at_risk"],
            "to": "churned",
            "when": (
                'subscription.direction == "cancelled" or customer.metadata.status == "churned" '
                "or activity.last_event_days_ago >= 90"
            ),
        },
        {
            "name": "reactivated",
            "from": ["churned"],
            "to": "adopting",
            "when": "activity.last_event_days_ago <= 7 and activity.events_recent >= 5",
        },
        {
            "name": "at_risk",
            "from": ["activated", "adopting", "power_user"],
            "to": "at_risk",
            "when": (
                'health.band in ["at_risk", "critical"] or signals.churn_risk >= 0.6 '
                "or problems.open_count >= 3 "
                'or (activity.trend == "declining" and activity.change_pct <= -40) '
                'or feedback.negative_trend == "rising"'
            ),
        },
        {
            # Stricter to leave than to enter, like the primary machine: no flapping.
            "name": "recovered",
            "from": ["at_risk"],
            "to": "adopting",
            "when": (
                'health.band in ["healthy", "watch"] and signals.churn_risk < 0.4 '
                'and problems.open_count < 2 and activity.trend != "declining"'
            ),
        },
        {
            "name": "activated",
            "from": ["new"],
            "to": "activated",
            "when": "activity.distinct_features >= 1 or activity.events_recent >= 3",
        },
        {
            "name": "adopting",
            "from": ["activated"],
            "to": "adopting",
            "when": "activity.distinct_features >= 3 or (activity.events_recent >= 10 and customer.age_days >= 7)",
        },
        {
            "name": "power_user",
            "from": ["adopting"],
            "to": "power_user",
            "when": 'activity.distinct_features >= 6 and activity.events_recent >= 25 and health.band == "healthy"',
        },
        {
            "name": "cooled",
            "from": ["power_user"],
            "to": "adopting",
            "when": 'activity.trend == "declining" and activity.change_pct <= -30',
        },
    ],
}

COMMERCIAL_TEMPLATE: dict[str, Any] = {
    "label": "Commercial",
    "description": "Where the customer is in the buying relationship, from trial to renewal.",
    "states": ["trial", "paying", "expanding", "renewing", "churned"],
    "initial": "trial",
    "transitions": [
        {
            "name": "churned",
            "from": ["trial", "paying", "expanding", "renewing"],
            "to": "churned",
            "when": 'subscription.direction == "cancelled" or customer.metadata.status == "churned"',
        },
        {
            "name": "won_back",
            "from": ["churned"],
            "to": "paying",
            "when": (
                'subscription.direction in ["started", "upgraded", "renewed"] '
                "and subscription.changed_days_ago <= 30"
            ),
        },
        {
            "name": "converted",
            "from": ["trial"],
            "to": "paying",
            "when": 'subscription.plan is set and not (subscription.plan in ["trial", "free"])',
        },
        {
            "name": "renewing",
            "from": ["paying", "expanding"],
            "to": "renewing",
            "when": 'intents.kinds contains "renewal" or customer.metadata.renewal_days <= 60',
        },
        {
            "name": "renewed",
            "from": ["renewing"],
            "to": "paying",
            "when": 'subscription.direction == "renewed" and subscription.changed_days_ago <= 30',
        },
        {
            "name": "expanding",
            "from": ["paying"],
            "to": "expanding",
            "when": (
                '(subscription.direction == "upgraded" and subscription.changed_days_ago <= 60) '
                'or intents.kinds contains "expansion" or signals.expansion_score >= 0.6'
            ),
        },
        {
            "name": "settled",
            "from": ["expanding"],
            "to": "paying",
            "when": (
                'intents.kinds does not contain "expansion" and signals.expansion_score < 0.3 '
                "and lifecycle.commercial.days_in_state >= 60"
            ),
        },
    ],
}

TRACK_TEMPLATES: dict[str, dict[str, Any]] = {
    "engagement": ENGAGEMENT_TEMPLATE,
    "commercial": COMMERCIAL_TEMPLATE,
}


class LifecycleError(ValueError):
    """A machine that cannot be compiled — refused when written, never at evaluation."""


@dataclass(slots=True, frozen=True)
class Transition:
    name: str
    sources: frozenset[str]  # empty means "from any state"
    target: str
    condition: Condition

    def applies_to(self, state: str | None) -> bool:
        return not self.sources or (state is not None and state in self.sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "from": sorted(self.sources) or [ANY],
            "to": self.target,
            "when": self.condition.text,
        }


@dataclass(slots=True)
class Step:
    """One move the machine made, with the evaluation that justified it."""

    transition: Transition
    previous: str | None
    evaluation: Evaluation

    @property
    def target(self) -> str:
        return self.transition.target

    def reason(self) -> str:
        return f"{self.transition.name}: {self.evaluation.explain()}"


@dataclass(slots=True)
class Lifecycle:
    states: tuple[str, ...]
    initial: str
    transitions: tuple[Transition, ...] = field(default_factory=tuple)

    def step(self, state: str | None, facts: CustomerFacts) -> Step | None:
        """The first transition out of ``state`` whose condition holds, if any."""
        for transition in self.transitions:
            if transition.target == state or not transition.applies_to(state):
                continue
            evaluation = transition.condition.evaluate(facts)
            if evaluation.matched:
                return Step(transition=transition, previous=state, evaluation=evaluation)
        return None

    def settle(self, state: str | None, facts: CustomerFacts) -> list[Step]:
        """Every move the customer makes now, stopping short of a cycle.

        ``state`` of ``None`` means the customer has never been placed; they start in the
        initial state and may move on from it in the same call.
        """
        current = state or self.initial
        visited = {current}
        steps: list[Step] = []
        for _ in range(MAX_HOPS):
            step = self.step(current, facts)
            if step is None or step.target in visited:
                break
            steps.append(step)
            visited.add(step.target)
            current = step.target
        return steps

    def as_dict(self) -> dict[str, Any]:
        return {
            "states": list(self.states),
            "initial": self.initial,
            "transitions": [transition.as_dict() for transition in self.transitions],
        }


def compile_lifecycle(raw: dict[str, Any] | None) -> Lifecycle:
    """Validate a machine. ``None`` or ``{}`` means the default."""
    spec = raw or DEFAULT_LIFECYCLE
    if not isinstance(spec, dict):
        raise LifecycleError("A lifecycle must be an object with states, initial and transitions.")

    states = spec.get("states")
    if not isinstance(states, list) or not states:
        raise LifecycleError("A lifecycle needs a non-empty list of states.")
    cleaned = [str(state).strip().lower() for state in states]
    for state in cleaned:
        if not state or not state.replace("_", "").isalnum():
            raise LifecycleError(f"{state!r} is not a valid state name — letters, digits and '_'.")
    if len(set(cleaned)) != len(cleaned):
        raise LifecycleError("State names must be unique.")
    if len(cleaned) > MAX_STATES:
        raise LifecycleError(f"At most {MAX_STATES} states.")

    initial = str(spec.get("initial") or cleaned[0]).strip().lower()
    if initial not in cleaned:
        raise LifecycleError(f"The initial state {initial!r} is not one of the states.")

    transitions_raw = spec.get("transitions") or []
    if not isinstance(transitions_raw, list):
        raise LifecycleError("Transitions must be a list.")
    if len(transitions_raw) > MAX_TRANSITIONS:
        raise LifecycleError(f"At most {MAX_TRANSITIONS} transitions.")

    transitions: list[Transition] = []
    names: set[str] = set()
    for index, entry in enumerate(transitions_raw):
        if not isinstance(entry, dict):
            raise LifecycleError(f"Transition {index + 1} must be an object.")
        target = str(entry.get("to") or "").strip().lower()
        if target not in cleaned:
            raise LifecycleError(f"Transition {index + 1} goes to {target!r}, which is not a state.")
        name = str(entry.get("name") or f"to_{target}").strip().lower()
        if name in names:
            raise LifecycleError(f"Two transitions are named {name!r}; names must be unique.")
        names.add(name)

        sources_raw = entry.get("from", [ANY])
        if isinstance(sources_raw, str):
            sources_raw = [sources_raw]
        sources = {str(item).strip().lower() for item in sources_raw}
        if ANY in sources:
            sources = set()
        unknown = sources - set(cleaned)
        if unknown:
            raise LifecycleError(
                f"Transition {name!r} leaves from {', '.join(sorted(unknown))}, which "
                f"{'is not a state' if len(unknown) == 1 else 'are not states'}."
            )
        if sources == {target}:
            raise LifecycleError(f"Transition {name!r} goes from {target!r} to itself.")

        when = entry.get("when")
        if not when:
            raise LifecycleError(f"Transition {name!r} needs a 'when' condition.")
        try:
            condition = compile_condition(when)
        except ConditionError as exc:
            raise LifecycleError(f"Transition {name!r}: {exc}") from exc
        transitions.append(Transition(name=name, sources=frozenset(sources), target=target, condition=condition))

    return Lifecycle(states=tuple(cleaned), initial=initial, transitions=tuple(transitions))


@dataclass(slots=True)
class Track:
    """A named machine beside the primary one."""

    name: str
    label: str
    machine: Lifecycle
    enabled: bool = True
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "label": self.label,
            "description": self.description,
            **self.machine.as_dict(),
        }


def _track_name(raw: Any) -> str:
    name = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not name or not name[0].isalpha() or not name.replace("_", "").isalnum() or len(name) > 40:
        raise LifecycleError(f"{raw!r} is not a valid track name — lowercase letters, digits and '_'.")
    if name == PRIMARY_TRACK:
        raise LifecycleError(f"{PRIMARY_TRACK!r} is the primary track; edit it under 'lifecycle'.")
    return name


def compile_tracks(raw: dict[str, Any] | None) -> dict[str, Track]:
    """Validate every extra track. Unlike the primary machine there is no default: a track
    exists because a project defined it, or took a template."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise LifecycleError("Lifecycle tracks must be an object of track name to machine.")
    if len(raw) > MAX_TRACKS:
        raise LifecycleError(f"At most {MAX_TRACKS} tracks beside the primary lifecycle.")
    tracks: dict[str, Track] = {}
    for key, spec in raw.items():
        name = _track_name(key)
        if name in tracks:
            raise LifecycleError(f"Two tracks are named {name!r}.")
        if not isinstance(spec, dict) or not spec.get("states"):
            raise LifecycleError(f"Track {name!r} needs states, an initial state and transitions.")
        try:
            machine = compile_lifecycle(spec)
        except LifecycleError as exc:
            raise LifecycleError(f"Track {name!r}: {exc}") from exc
        tracks[name] = Track(
            name=name,
            label=str(spec.get("label") or name.replace("_", " ").title())[:60],
            machine=machine,
            enabled=spec.get("enabled", True) is not False,
            description=(str(spec["description"])[:300] if spec.get("description") else None),
        )
    return tracks
