"""Should an agent do this, to this customer, right now?

An agent that is about to offer an upgrade does not know the customer filed two unresolved
problems yesterday. Memora does. This module turns what Memora knows into a decision an
agent can ask for *before* it acts — ``allow``, ``require_approval`` or ``deny`` — with the
reason and the evidence, so "my AI agent did something it shouldn't" becomes a refusal the
agent received and could explain.

Three layers, evaluated together, strictest decision wins (deny > require_approval > allow):

1. **The agent's profile** — actions it may never take, or the only ones it may.
2. **Built-in rules** — the judgement calls nearly every SaaS team would make, written in
   code because some compare one fact with another (the channel an agent wants to use with
   the channel the customer prefers), which a condition over literals cannot express safely.
   Each can be switched off per project.
3. **Project rules** — written in the condition language (§27.2), with the proposed action
   available as ``request.*`` facts: ``request.amount > 500 and health.band == "at_risk"``.

Every rule that fired is reported, not just the deciding one: an agent — or the person
reviewing an approval — should see every reason at once rather than discovering them one
retry at a time.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from memory_engine.conditions import (
    Condition,
    ConditionError,
    compile_condition,
    sanitize_evaluation,
)
from memory_engine.facts import CustomerFacts
from nlp.tokenize import root, surface_words

ALLOW, REQUIRE_APPROVAL, DENY = "allow", "require_approval", "deny"
_RANK = {ALLOW: 0, REQUIRE_APPROVAL: 1, DENY: 2}

# Action families the built-in rules recognise. Any other action name is accepted — it is
# simply judged by the profile and the project's own rules.
SELLING = frozenset({"offer_upgrade", "offer_expansion", "upsell", "cross_sell", "offer_add_on"})
PROMOTION = frozenset({"send_marketing", "send_promotion", "request_review", "request_referral"})
CONTACT = frozenset({"contact_customer", "send_message", "send_email", "call_customer", *PROMOTION})
CLOSING = frozenset({"close_ticket", "mark_resolved", "resolve_ticket"})
MONEY = frozenset({"offer_discount", "issue_credit", "process_refund", "waive_fee"})
ACCOUNT = frozenset({"cancel_subscription", "downgrade_plan", "change_plan", "delete_account", "pause_subscription"})

ACTION_CATALOG: dict[str, str] = {
    **dict.fromkeys(sorted(SELLING), "selling"),
    **dict.fromkeys(sorted(PROMOTION), "promotion"),
    **dict.fromkeys(sorted(CONTACT - PROMOTION), "contact"),
    **dict.fromkeys(sorted(CLOSING), "closing"),
    **dict.fromkeys(sorted(MONEY), "money"),
    **dict.fromkeys(sorted(ACCOUNT), "account"),
    "escalate": "support",
    "create_ticket": "support",
    "schedule_call": "contact",
}

BUILTIN_RULES: dict[str, str] = {
    "open_problem_blocks_selling": "Do not sell to a customer with an unresolved problem.",
    "at_risk_blocks_selling": "Do not sell to a customer who is at risk.",
    "churn_intent_blocks_promotion": "Do not market to a customer who has said they may leave.",
    "channel_preference": "Contact a customer only on the channel they prefer.",
    "unresolved_problem_blocks_closing": "Do not close a ticket whose problem is still open.",
    "money_requires_approval": "Discounts, credits and refunds need a person.",
    "account_change_requires_approval": "Cancelling, downgrading or deleting needs a person.",
}

MAX_PROJECT_RULES = 30

# How the money actions read in a sentence.
_MONEY_PHRASES = {
    "offer_discount": "A discount",
    "issue_credit": "A credit",
    "process_refund": "A refund",
    "waive_fee": "Waiving a fee",
}


class GuardrailError(ValueError):
    """A guardrail configuration that cannot be compiled — refused when written."""


@dataclass(slots=True)
class Reason:
    rule: str
    source: str  # "profile" | "builtin" | "project" | "approval"
    decision: str
    explanation: str
    evidence: list[str] = field(default_factory=list)
    evaluation: dict[str, Any] | None = None
    # The same explanation without any value quoted from memory, for a reader who may not
    # see everything. Only reasons that quote something need one.
    redacted_explanation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rule": self.rule,
            "source": self.source,
            "decision": self.decision,
            "explanation": self.explanation,
            "evidence": self.evidence[:10],
            "evaluation": self.evaluation,
        }
        if self.redacted_explanation:
            payload["redacted_explanation"] = self.redacted_explanation
        return payload


@dataclass(slots=True)
class Verdict:
    decision: str
    reasons: list[Reason]

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    @property
    def evidence(self) -> list[str]:
        return list(dict.fromkeys(ident for reason in self.reasons for ident in reason.evidence))

    def summary(self) -> str:
        deciding = [reason for reason in self.reasons if reason.decision == self.decision]
        if not deciding:
            return "Allowed: no rule objects."
        return deciding[0].explanation


@dataclass(slots=True, frozen=True)
class ProjectRule:
    name: str
    actions: frozenset[str]  # empty = every action
    condition: Condition
    decision: str
    message: str

    def applies_to(self, action: str) -> bool:
        return not self.actions or action in self.actions

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "actions": sorted(self.actions) or ["*"],
            "when": self.condition.text,
            "decision": self.decision,
            "message": self.message,
        }


@dataclass(slots=True)
class Guardrails:
    disabled: frozenset[str] = frozenset()
    rules: tuple[ProjectRule, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"disabled": sorted(self.disabled), "rules": [rule.as_dict() for rule in self.rules]}


@dataclass(slots=True, frozen=True)
class Profile:
    """The part of an agent profile a check needs."""

    name: str
    allowed_actions: frozenset[str] = frozenset()
    denied_actions: frozenset[str] = frozenset()


def normalise_action(action: str) -> str:
    return action.strip().lower().replace("-", "_").replace(" ", "_")


def compile_guardrails(raw: dict[str, Any] | None) -> Guardrails:
    if not raw:
        return Guardrails()
    if not isinstance(raw, dict):
        raise GuardrailError("Guardrails must be an object with 'disabled' and 'rules'.")
    disabled = {str(item).strip() for item in raw.get("disabled") or []}
    unknown = disabled - set(BUILTIN_RULES)
    if unknown:
        raise GuardrailError(
            f"Unknown built-in rule(s): {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(BUILTIN_RULES)}."
        )
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise GuardrailError("'rules' must be a list.")
    if len(rules_raw) > MAX_PROJECT_RULES:
        raise GuardrailError(f"At most {MAX_PROJECT_RULES} project rules.")
    rules: list[ProjectRule] = []
    names: set[str] = set()
    for index, entry in enumerate(rules_raw, start=1):
        if not isinstance(entry, dict):
            raise GuardrailError(f"Rule {index} must be an object.")
        name = str(entry.get("name") or f"rule_{index}").strip().lower().replace(" ", "_")
        if name in names or name in BUILTIN_RULES:
            raise GuardrailError(f"Rule name {name!r} is already used.")
        names.add(name)
        decision = str(entry.get("decision") or DENY).strip().lower()
        if decision not in (DENY, REQUIRE_APPROVAL):
            raise GuardrailError(f"Rule {name!r}: decision must be 'deny' or 'require_approval'.")
        actions_raw = entry.get("actions") or ["*"]
        if isinstance(actions_raw, str):
            actions_raw = [actions_raw]
        actions = {normalise_action(str(item)) for item in actions_raw}
        if "*" in actions:
            actions = set()
        when = entry.get("when")
        if not when:
            raise GuardrailError(f"Rule {name!r} needs a 'when' condition.")
        try:
            condition = compile_condition(when)
        except ConditionError as exc:
            raise GuardrailError(f"Rule {name!r}: {exc}") from exc
        message = str(entry.get("message") or "").strip() or f"Blocked by the project rule {name!r}."
        rules.append(ProjectRule(name, frozenset(actions), condition, decision, message))
    return Guardrails(disabled=frozenset(disabled), rules=tuple(rules))


def request_facts(action: str, request: dict[str, Any]) -> dict[str, Any]:
    """The proposed action as facts a condition can read."""
    extra = {str(key): value for key, value in (request or {}).items()}
    values: dict[str, Any] = {
        "request.action": action,
        "request.channel": str(extra.pop("channel")).strip().lower() if extra.get("channel") else None,
        "request.amount": _number(extra.pop("amount", None)),
        "request.topic": surface_words(str(extra.pop("topic"))) if extra.get("topic") else None,
        "request.plan": str(extra.pop("plan")).strip().lower() if extra.get("plan") else None,
        "request.extra": extra,
    }
    return values


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def check(
    *,
    action: str,
    request: dict[str, Any],
    facts: CustomerFacts,
    visible: CustomerFacts | None = None,
    guardrails: Guardrails | None = None,
    profile: Profile | None = None,
    sanitize: bool = False,
) -> Verdict:
    """Decide. ``facts`` are the full document — a decision must see everything; ``visible``
    is what the caller may read, and is what explanations quote from.

    The proposed action is added to both documents as ``request.*`` facts, so a check's fact
    documents are its own and should not be reused for anything else.
    """
    action = normalise_action(action)
    guardrails = guardrails or Guardrails()
    visible = visible or facts
    for name, value in request_facts(action, request).items():
        facts.values[name] = value
        visible.values[name] = value

    reasons: list[Reason] = []
    if profile is not None:
        reasons.extend(_profile(action, profile))
    reasons.extend(_builtins(action, facts, visible, guardrails.disabled))
    for rule in guardrails.rules:
        if not rule.applies_to(action):
            continue
        evaluation = rule.condition.evaluate(facts)
        if evaluation.matched:
            trace = evaluation.as_dict()
            reasons.append(
                Reason(
                    rule=rule.name,
                    source="project",
                    decision=rule.decision,
                    explanation=rule.message,
                    evidence=_visible_ids(evaluation.evidence, facts, visible),
                    evaluation=sanitize_evaluation(trace) if sanitize else trace,
                )
            )

    decision = max((reason.decision for reason in reasons), key=_RANK.get, default=ALLOW)
    reasons.sort(key=lambda reason: -_RANK[reason.decision])
    return Verdict(decision=decision, reasons=reasons)


def _profile(action: str, profile: Profile) -> Iterable[Reason]:
    if action in profile.denied_actions:
        yield Reason(
            rule="profile_denied_action",
            source="profile",
            decision=DENY,
            explanation=f"The {profile.name!r} profile may never take the action {action!r}.",
        )
    elif profile.allowed_actions and action not in profile.allowed_actions:
        yield Reason(
            rule="profile_action_not_allowed",
            source="profile",
            decision=DENY,
            explanation=(
                f"The {profile.name!r} profile may only take: {', '.join(sorted(profile.allowed_actions))}."
            ),
        )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _builtins(action: str, facts: CustomerFacts, visible: CustomerFacts, disabled: frozenset[str]) -> Iterable[Reason]:
    on = lambda name: name not in disabled  # noqa: E731

    open_problems = int(facts.get("problems.open_count") or 0)
    if on("open_problem_blocks_selling") and action in SELLING and open_problems:
        yield Reason(
            rule="open_problem_blocks_selling",
            source="builtin",
            decision=DENY,
            explanation=f"The customer has {_plural(open_problems, 'open problem')}; resolve them before selling.",
            evidence=visible.evidence_for("problems.open_count"),
        )

    band = facts.get("health.band")
    state = facts.get("state.current")
    if on("at_risk_blocks_selling") and action in SELLING and (band in ("at_risk", "critical") or state == "at_risk"):
        why = f"health is {band}" if band in ("at_risk", "critical") else "their lifecycle state is at_risk"
        yield Reason(
            rule="at_risk_blocks_selling",
            source="builtin",
            decision=DENY,
            explanation=f"The customer is at risk ({why}); selling now would read as not listening.",
            evidence=visible.evidence_for("health.band"),
        )

    kinds = facts.get("intents.kinds") or []
    if on("churn_intent_blocks_promotion") and action in (PROMOTION | SELLING) and "cancellation" in kinds:
        yield Reason(
            rule="churn_intent_blocks_promotion",
            source="builtin",
            decision=DENY,
            explanation="The customer has said they may leave; do not promote to them.",
            evidence=visible.evidence_for("intents.kinds", "cancellation"),
        )

    wanted = facts.get("request.channel")
    preferred = facts.get("preferences.channel")
    if on("channel_preference") and action in CONTACT and wanted and preferred and _channel(wanted) != _channel(preferred):
        shown = visible.get("preferences.channel")
        withheld = f"The customer's contact preference does not allow {wanted}."
        yield Reason(
            rule="channel_preference",
            source="builtin",
            decision=DENY,
            explanation=f"The customer prefers {shown}, not {wanted}." if shown else withheld,
            evidence=visible.evidence_for("preferences.channel"),
            redacted_explanation=withheld,
        )

    if on("unresolved_problem_blocks_closing") and action in CLOSING and open_problems:
        topic = facts.get("request.topic") or []
        topic_roots = {root(word) for word in topic}
        matching = [
            ident
            for word in topic_roots
            for ident in facts.evidence_by_value.get("problems.terms", {}).get(word, [])
        ]
        if topic_roots and matching:
            yield Reason(
                rule="unresolved_problem_blocks_closing",
                source="builtin",
                decision=DENY,
                explanation="The problem this ticket is about is still open in memory.",
                evidence=_visible_ids(matching, facts, visible),
            )
        elif not topic_roots:
            yield Reason(
                rule="unresolved_problem_blocks_closing",
                source="builtin",
                decision=REQUIRE_APPROVAL,
                explanation=(
                    f"The customer has {_plural(open_problems, 'open problem')} and the request does not "
                    "say which one this ticket is about — a person should confirm it is resolved."
                ),
                evidence=visible.evidence_for("problems.open_count"),
            )

    if on("money_requires_approval") and action in MONEY:
        amount = facts.get("request.amount")
        what = _MONEY_PHRASES.get(action, action.replace("_", " "))
        yield Reason(
            rule="money_requires_approval",
            source="builtin",
            decision=REQUIRE_APPROVAL,
            explanation=(
                f"{what} of {amount:g} needs a person's approval."
                if isinstance(amount, (int, float))
                else f"{what} needs a person's approval."
            ),
        )

    if on("account_change_requires_approval") and action in ACCOUNT:
        yield Reason(
            rule="account_change_requires_approval",
            source="builtin",
            decision=REQUIRE_APPROVAL,
            explanation=f"{action.replace('_', ' ').capitalize()} changes the customer's account; a person should confirm.",
        )


def _visible_ids(ids: Iterable[str], facts: CustomerFacts, visible: CustomerFacts) -> list[str]:
    """Evidence a caller may be shown: what their view was redacted for drops out."""
    unique = list(dict.fromkeys(ids))
    if visible is facts:
        return unique
    return [ident for ident in unique if ident not in visible.hidden_ids]


def redact_reason(reason: dict[str, Any], hidden: frozenset[str] | set[str]) -> dict[str, Any]:
    """A stored reason as a reader who may not see everything sees it: evidence without
    hidden ids, the trace without actual values, and — only when what it quotes came from
    a memory now hidden from them — the explanation without the quoted value."""
    evaluation = reason.get("evaluation")
    evidence = list(reason.get("evidence") or [])
    kept = [ident for ident in evidence if ident not in hidden]
    explanation = reason.get("explanation", "")
    if len(kept) < len(evidence) and reason.get("redacted_explanation"):
        explanation = reason["redacted_explanation"]
    return {
        **reason,
        "explanation": explanation,
        "evidence": kept,
        "evaluation": sanitize_evaluation(evaluation) if evaluation else None,
    }


def _channel(value: str) -> str:
    """"WhatsApp", "whatsapp" and "whats app" are one channel."""
    return "".join(str(value).lower().split()).replace("-", "")


def catalog() -> dict[str, Any]:
    return {
        "actions": [{"action": action, "family": family} for action, family in ACTION_CATALOG.items()],
        "builtin_rules": [{"rule": rule, "description": text} for rule, text in BUILTIN_RULES.items()],
        "decisions": [ALLOW, REQUIRE_APPROVAL, DENY],
    }


def evidence_of(reasons: Sequence[Reason]) -> list[str]:
    return list(dict.fromkeys(ident for reason in reasons for ident in reason.evidence))
