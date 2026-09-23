"""Statement templates for structured events.

Most SaaS events carry no prose at all — `subscription_downgraded` with `{"plan": "Starter"}`
says something important and says it in fields. These templates turn those fields into
memory sentences deterministically, which is both cheaper and more reliable than asking a
model to describe a dictionary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from common.enums import MemoryType
from nlp.lexicon import INTEGRATIONS, PLANS


@dataclass(slots=True)
class TemplatedMemory:
    type: MemoryType
    content: str
    importance: float
    confidence: float
    rule: str
    attributes: dict[str, Any]


def _label(value: Any, *, title: bool = True) -> str:
    text = str(value).replace("_", " ").strip()
    known = INTEGRATIONS.get(text.lower()) or PLANS.get(text.lower())
    if known:
        return known
    return text.title() if title and text.islower() else text


def _get(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = (data or {}).get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def _subscription(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    plan = _get(data, "plan", "new_plan", "to_plan", "current_plan")
    previous = _get(data, "previous_plan", "old_plan", "from_plan")
    reason = _get(data, "reason", "cancellation_reason", "note")
    direction = (
        "downgraded" if "downgrad" in event_type else
        "upgraded" if "upgrad" in event_type else
        "cancelled" if "cancel" in event_type else "changed"
    )
    if plan is None and previous is None and direction == "changed":
        return None

    if direction == "cancelled":
        sentence = "The customer cancelled their subscription"
        if plan or previous:
            sentence += f" ({_label(plan or previous)} plan)"
    elif previous and plan:
        sentence = f"The customer {direction} from the {_label(previous)} plan to the {_label(plan)} plan"
    elif plan:
        sentence = f"The customer {direction} to the {_label(plan)} plan"
    else:
        sentence = f"The customer {direction} their subscription from the {_label(previous)} plan"
    if reason:
        sentence += f", citing: {str(reason).rstrip('.')}"

    importance = {"downgraded": 0.92, "cancelled": 0.98, "upgraded": 0.8}.get(direction, 0.75)
    return TemplatedMemory(
        type=MemoryType.SUBSCRIPTION,
        content=sentence + ".",
        importance=importance,
        confidence=0.97,
        rule=f"template:subscription_{direction}",
        attributes={"plan": plan, "previous_plan": previous, "direction": direction},
    )


def _integration(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    name = _get(data, "integration", "provider", "app", "service")
    if name is None:
        return None
    failed = any(marker in event_type for marker in ("fail", "error", "disconnect", "revoke"))
    error = _get(data, "error", "error_message", "message", "reason")

    if failed:
        sentence = f"The customer's {_label(name)} integration failed"
        if error:
            sentence += f": {str(error).rstrip('.')}"
        return TemplatedMemory(
            type=MemoryType.PROBLEM,
            content=sentence + ".",
            importance=0.85,
            confidence=0.96,
            rule="template:integration_failed",
            attributes={"integration": _label(name), "error": error},
        )
    return TemplatedMemory(
        type=MemoryType.FACT,
        content=f"The customer uses the {_label(name)} integration.",
        importance=0.6,
        confidence=0.95,
        rule="template:integration_connected",
        attributes={"integration": _label(name)},
    )


def _payment(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    amount = _get(data, "amount", "total", "value")
    currency = _get(data, "currency") or ""
    reason = _get(data, "reason", "failure_reason", "error", "decline_code")
    failed = "fail" in event_type or "declin" in event_type

    if failed:
        sentence = "The customer's payment failed"
        if amount:
            sentence += f" for {currency}{amount}".rstrip()
        if reason:
            sentence += f" ({reason})"
        return TemplatedMemory(
            type=MemoryType.PROBLEM,
            content=sentence + ".",
            importance=0.88,
            confidence=0.97,
            rule="template:payment_failed",
            attributes={"amount": amount, "reason": reason},
        )
    if "refund" in event_type:
        return TemplatedMemory(
            type=MemoryType.SUBSCRIPTION,
            content=f"The customer was refunded {currency}{amount}." if amount else "The customer received a refund.",
            importance=0.8,
            confidence=0.95,
            rule="template:refund",
            attributes={"amount": amount},
        )
    return None


def _feature(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    feature = _get(data, "feature", "feature_name", "action")
    if feature is None:
        return None
    count = _get(data, "count", "usage_count")
    sentence = f"The customer uses the {_label(feature)} feature"
    if isinstance(count, (int, float)) and count > 1:
        sentence += f" ({int(count)} times recorded)"
    return TemplatedMemory(
        type=MemoryType.BEHAVIOR,
        content=sentence + ".",
        importance=0.35,
        confidence=0.9,
        rule="template:feature_used",
        attributes={"feature": _label(feature)},
    )


def _purchase(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    amount = _get(data, "amount", "total", "value")
    currency = _get(data, "currency") or ""
    product = _get(data, "product", "item", "sku")
    if amount is None and product is None:
        return None
    parts = ["The customer placed an order"]
    if product:
        parts.append(f"for {_label(product)}")
    if amount:
        parts.append(f"worth {currency}{amount}")
    return TemplatedMemory(
        type=MemoryType.BEHAVIOR,
        content=" ".join(parts) + ".",
        importance=0.65,
        confidence=0.95,
        rule="template:purchase",
        attributes={"amount": amount, "product": product},
    )


def _nps(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    score = _get(data, "score", "rating", "nps")
    if score is None:
        return None
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return None
    band = "promoter" if numeric >= 9 else "passive" if numeric >= 7 else "detractor"
    importance = 0.9 if band == "detractor" else 0.7
    return TemplatedMemory(
        type=MemoryType.FEEDBACK,
        content=f"The customer gave a satisfaction score of {numeric:g} ({band}).",
        importance=importance,
        confidence=0.97,
        rule="template:nps",
        attributes={"score": numeric, "band": band},
    )


def _cancellation(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    reason = _get(data, "reason", "cancellation_reason", "feedback")
    sentence = "The customer requested cancellation"
    if reason:
        sentence += f", citing: {str(reason).rstrip('.')}"
    return TemplatedMemory(
        type=MemoryType.INTENT,
        content=sentence + ".",
        importance=0.98,
        confidence=0.97,
        rule="template:cancellation_requested",
        attributes={"reason": reason},
    )


def _goal(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    goal = _get(data, "goal", "objective", "target", "description")
    if goal is None:
        return None
    due = _get(data, "due_date", "deadline", "target_date")
    sentence = f"The customer's goal is {str(goal).rstrip('.')}"
    if due:
        sentence += f" (target {due})"
    return TemplatedMemory(
        type=MemoryType.GOAL,
        content=sentence + ".",
        importance=0.75,
        confidence=0.95,
        rule="template:goal",
        attributes={"goal": goal, "due": due},
    )


def _profile(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    company = _get(data, "company", "company_name", "organisation", "organization")
    role = _get(data, "role", "job_title", "title")
    industry = _get(data, "industry", "vertical")
    size = _get(data, "team_size", "employees", "seats")
    parts: list[str] = []
    if role:
        parts.append(f"works as {role}")
    if company:
        parts.append(f"at {_label(company, title=False)}")
    if industry:
        parts.append(f"in the {industry} industry")
    if size:
        parts.append(f"with a team of {size}")
    if not parts:
        return None
    return TemplatedMemory(
        type=MemoryType.FACT,
        content="The customer " + " ".join(parts) + ".",
        importance=0.6,
        confidence=0.95,
        rule="template:profile",
        attributes={"company": company, "role": role, "industry": industry},
    )


def _support(data: dict[str, Any], event_type: str) -> TemplatedMemory | None:
    """A support contact is itself a fact, even when the message body is empty."""
    subject = _get(data, "subject", "title", "topic")
    if not subject:
        return None
    return TemplatedMemory(
        type=MemoryType.PROBLEM,
        content=f"The customer contacted support about: {str(subject).rstrip('.')}.",
        importance=0.75,
        confidence=0.9,
        rule="template:support_subject",
        attributes={"subject": subject},
    )


# event-type marker → builder. Checked in order; the first match wins.
_BUILDERS: tuple[tuple[tuple[str, ...], Callable[[dict[str, Any], str], TemplatedMemory | None]], ...] = (
    (("cancellation_requested", "cancel_requested", "churn_requested"), _cancellation),
    (("subscription", "plan_changed", "upgrade", "downgrade", "trial_converted"), _subscription),
    (("integration", "connector", "oauth"), _integration),
    (("payment", "invoice", "charge", "refund", "billing"), _payment),
    (("feature", "action_performed"), _feature),
    (("purchase", "order", "checkout"), _purchase),
    (("nps", "csat", "rating", "review"), _nps),
    (("goal", "objective"), _goal),
    (("profile", "account_updated", "identify", "signup", "registered"), _profile),
    (("support", "ticket", "conversation"), _support),
)


def build(event_type: str, data: dict[str, Any]) -> TemplatedMemory | None:
    """The structured memory implied by an event's type and payload, if any."""
    normalised = (event_type or "").strip().lower()
    for markers, builder in _BUILDERS:
        if any(marker in normalised for marker in markers):
            memory = builder(data or {}, normalised)
            if memory is not None:
                return memory
    return None
