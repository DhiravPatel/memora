"""Next best action: what to do about a customer, and why.

A score tells you something is wrong; it does not tell you what to do on Monday morning.
This module turns the memory graph and the signal report into a short, ranked list of
actions, each carrying the memories that justify it. Every rule is a pure function over
the same inputs, so a recommendation can be replayed, argued with, and unit-tested — and
nothing is ever suggested that the customer's own history does not support.

The list is deliberately short. Ten recommendations is a backlog; three is a plan.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from common.enums import MemoryType, SignalDirection
from common.time import days_between, ensure_utc, utcnow
from memory_engine.analytics.signals import GoalSnapshot, SignalReport, ago, mask_quotes
from memory_engine.consolidation.rules import without_recurrence_note
from nlp.answer import MemoryView
from nlp.lexicon import CHANNELS

MAX_RECOMMENDATIONS = 5

# Urgency bands, so the UI and the SDK agree on what "now" means.
PRIORITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.75, "now"),
    (0.45, "soon"),
    (0.0, "when_you_can"),
)


def priority_for(urgency: float) -> str:
    for threshold, name in PRIORITY_BANDS:
        if urgency >= threshold:
            return name
    return "when_you_can"


@dataclass(slots=True, frozen=True)
class Recommendation:
    key: str
    action: str
    rationale: str
    category: str
    urgency: float
    memory_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    playbook: tuple[str, ...] = ()
    # (id, words) for every memory or goal whose words appear in the action or rationale,
    # so a reader who may not see that memory gets the recommendation without the quote.
    quotes: tuple[tuple[str, str], ...] = ()

    @property
    def priority(self) -> str:
        return priority_for(self.urgency)

    def cited_ids(self) -> set[str]:
        return {*self.memory_ids, *self.goal_ids, *(ident for ident, _ in self.quotes)}

    def redacted(self, hidden: AbstractSet[str]) -> Recommendation:
        """As a reader who may not see ``hidden`` sees it: still recommended — the
        judgement was made over everything — but without the hidden words or ids."""
        if not hidden or not (self.cited_ids() & hidden):
            return self
        return replace(
            self,
            action=mask_quotes(self.action, self.quotes, hidden),
            rationale=mask_quotes(self.rationale, self.quotes, hidden),
            memory_ids=tuple(ident for ident in self.memory_ids if ident not in hidden),
            goal_ids=tuple(ident for ident in self.goal_ids if ident not in hidden),
            quotes=tuple(quote for quote in self.quotes if quote[0] not in hidden),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "action": self.action,
            "rationale": self.rationale,
            "category": self.category,
            "urgency": round(self.urgency, 3),
            "priority": self.priority,
            "memory_ids": list(self.memory_ids[:5]),
            "goal_ids": list(self.goal_ids[:5]),
            "signals": list(self.signals),
            "playbook": list(self.playbook),
        }


@dataclass(slots=True)
class Context:
    """Everything the rules are allowed to look at."""

    memories: Sequence[MemoryView]
    report: SignalReport
    goals: Sequence[GoalSnapshot] = field(default_factory=tuple)
    health_score: float = 70.0
    customer_name: str | None = None
    now: datetime = field(default_factory=utcnow)

    def of_type(self, memory_type: MemoryType) -> list[MemoryView]:
        return [m for m in self.memories if str(m.type) == memory_type.value]

    def signal(self, key: str) -> Any:
        for signal in self.report.signals:
            if signal.key == key:
                return signal
        return None

    def open_problems(self) -> list[MemoryView]:
        return [m for m in self.of_type(MemoryType.PROBLEM) if not m.is_resolved]

    def age_days(self, memory: MemoryView) -> float:
        return days_between(ensure_utc(memory.first_seen_at), self.now)


Rule = Callable[[Context], list[Recommendation]]


def _clip(text: str, limit: int = 70) -> str:
    # The recurrence note repeats what a rationale says with the count.
    text = " ".join(without_recurrence_note(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- rules


def resolve_open_problems(context: Context) -> list[Recommendation]:
    """The oldest or most urgent unresolved problem is almost always the right next move."""
    problems = context.open_problems()
    if not problems:
        return []

    def weight(memory: MemoryView) -> float:
        age = min(1.0, context.age_days(memory) / 30)
        return 0.5 * memory.urgency + 0.3 * age + 0.2 * min(1.0, memory.evidence_count / 3)

    worst = max(problems, key=weight)
    urgency = round(min(1.0, 0.45 + 0.55 * weight(worst)), 3)
    return [
        Recommendation(
            key="resolve_open_problem",
            action=f"Resolve: {_clip(worst.content)}",
            quotes=((worst.id, _clip(worst.content)),),
            rationale=(
                f"Reported {ago(context.age_days(worst))} and still open"
                + (f", mentioned {worst.evidence_count} times" if worst.evidence_count > 1 else "")
                + "."
            ),
            category="support",
            urgency=urgency,
            memory_ids=(worst.id,),
            signals=tuple(
                signal.key
                for signal in context.report.signals
                if signal.key in ("ageing_open_problem", "escalating_problems", "repeat_problem")
            ),
            playbook=(
                "Check whether engineering already has a fix in flight.",
                "Reply to the customer with a status, even if the answer is 'not yet'.",
                "Record the outcome as an event so the memory closes itself.",
            ),
        )
    ]


def escalate_repeat_problems(context: Context) -> list[Recommendation]:
    repeats = [m for m in context.open_problems() if m.evidence_count >= 3]
    if not repeats:
        return []
    worst = max(repeats, key=lambda memory: memory.evidence_count)
    return [
        Recommendation(
            key="escalate_repeat_problem",
            action=f"Escalate: {_clip(worst.content)}",
            quotes=((worst.id, _clip(worst.content)),),
            rationale=(
                f"Reported {worst.evidence_count} separate times without being closed out — "
                "first-line support has not been enough."
            ),
            category="support",
            urgency=round(min(1.0, 0.6 + 0.08 * worst.evidence_count), 3),
            memory_ids=(worst.id,),
            signals=("repeat_problem",),
            playbook=(
                "Raise it with an owner who can change the product, not just the ticket.",
                "Tell the customer it has been escalated, and to whom.",
            ),
        )
    ]


def retention_outreach(context: Context) -> list[Recommendation]:
    signal = context.signal("churn_language")
    if signal is None:
        return []
    return [
        Recommendation(
            key="retention_outreach",
            action="Get on a call about the renewal",
            rationale=f"{signal.rationale.capitalize()}.",
            quotes=signal.quotes,
            category="retention",
            urgency=round(min(1.0, 0.7 + 0.3 * signal.strength), 3),
            memory_ids=signal.memory_ids,
            signals=("churn_language",),
            playbook=(
                "Read the problem history first; do not make them repeat it.",
                "Lead with what has been fixed since they raised it.",
                "Bring a concrete commitment — a date, an owner, or a credit.",
            ),
        )
    ]


def re_engage(context: Context) -> list[Recommendation]:
    silence = context.signal("silence")
    decay = context.signal("engagement_decay")
    signal = silence or decay
    if signal is None:
        return []
    return [
        Recommendation(
            key="re_engage",
            action="Reach out before the next renewal cycle",
            rationale=f"{signal.rationale.capitalize()}.",
            quotes=signal.quotes,
            category="retention",
            urgency=round(min(0.8, 0.35 + 0.45 * signal.strength), 3),
            signals=(signal.key,),
            playbook=(
                "Open with something specific from their history, not a check-in template.",
                "Offer the one thing they last said they were trying to do.",
            ),
        )
    ]


def unblock_goals(context: Context) -> list[Recommendation]:
    """A goal nobody has moved is the clearest invitation to be useful."""
    stalled = [goal for goal in context.goals if goal.status == "stalled"]
    if not stalled:
        return []
    goal = max(stalled, key=lambda item: days_between(ensure_utc(item.last_signal_at), context.now))
    idle_days = int(days_between(ensure_utc(goal.last_signal_at), context.now))
    return [
        Recommendation(
            key="unblock_goal",
            action=f"Help them finish: {_clip(goal.statement)}",
            quotes=((goal.id, _clip(goal.statement)),),
            rationale=(
                f"They said this {ago(idle_days)} and nothing has moved it since "
                f"({int(goal.progress * 100)}% of the way there)."
            ),
            category="onboarding",
            urgency=round(min(0.8, 0.4 + 0.4 * min(1.0, idle_days / 60)), 3),
            goal_ids=(goal.id,),
            signals=("goal_stalled",),
            playbook=(
                "Ask what stopped, rather than re-explaining the feature.",
                "Send the one doc or config that unblocks the next step.",
            ),
        )
    ]


def expansion_offer(context: Context) -> list[Recommendation]:
    signal = context.signal("expansion_intent")
    if signal is None or context.open_problems():
        # Never pitch to someone whose last message was a complaint.
        return []
    return [
        Recommendation(
            key="expansion_offer",
            action="Follow up on what they asked to add",
            rationale="They raised upgrading or expanding and nothing of theirs is broken.",
            category="expansion",
            urgency=round(min(0.8, 0.45 + 0.35 * signal.strength), 3),
            memory_ids=signal.memory_ids,
            signals=("expansion_intent",),
            playbook=(
                "Quote the exact thing they named, not the whole catalogue.",
                "Include what it changes for the goal they already stated.",
            ),
        )
    ]


def ask_for_advocacy(context: Context) -> list[Recommendation]:
    signal = context.signal("advocacy")
    if signal is None or context.health_score < 75 or context.open_problems():
        return []
    achieved = [goal for goal in context.goals if goal.status == "achieved"]
    rationale = "They are happy and have nothing open."
    quotes: tuple[tuple[str, str], ...] = ()
    if achieved:
        rationale = f"They reached “{_clip(achieved[0].statement, 50)}” and said so in feedback."
        quotes = ((achieved[0].id, _clip(achieved[0].statement, 50)),)
    return [
        Recommendation(
            key="ask_for_advocacy",
            action="Ask for a reference or case study",
            rationale=rationale,
            category="relationship",
            urgency=0.35,
            memory_ids=signal.memory_ids,
            goal_ids=tuple(goal.id for goal in achieved[:1]),
            signals=("advocacy", "goal_achieved") if achieved else ("advocacy",),
            quotes=quotes,
            playbook=(
                "Name the specific outcome you want them to talk about.",
                "Offer to write the first draft for them.",
            ),
        )
    ]


def close_the_loop(context: Context) -> list[Recommendation]:
    """A problem the system believes is resolved, which the customer never confirmed."""
    resolved = [
        memory
        for memory in context.of_type(MemoryType.PROBLEM)
        if memory.is_resolved and context.age_days(memory) <= 30
    ]
    if not resolved:
        return []
    feedback_since = [
        memory
        for memory in context.of_type(MemoryType.FEEDBACK)
        if ensure_utc(memory.last_seen_at) > ensure_utc(resolved[0].last_seen_at)
    ]
    if feedback_since:
        return []
    return [
        Recommendation(
            key="close_the_loop",
            action=f"Confirm the fix landed: {_clip(resolved[0].content)}",
            quotes=((resolved[0].id, _clip(resolved[0].content)),),
            rationale="Recorded as resolved, but they have not said anything since.",
            category="support",
            urgency=0.3,
            memory_ids=(resolved[0].id,),
            playbook=("One line asking whether it is genuinely sorted on their side.",),
        )
    ]


def respect_contact_preference(context: Context) -> list[Recommendation]:
    """If they told us how to contact them, that constrains every other action here."""
    preferences = [
        memory
        for memory in context.of_type(MemoryType.PREFERENCE)
        if any(channel in memory.content.lower() for channel in CHANNELS)
    ]
    if not preferences:
        return []
    return [
        Recommendation(
            key="respect_contact_preference",
            action=f"Use their stated channel: {_clip(preferences[0].content, 60)}",
            quotes=((preferences[0].id, _clip(preferences[0].content, 60)),),
            rationale="They have told us how they want to be contacted.",
            category="relationship",
            urgency=0.25,
            memory_ids=tuple(memory.id for memory in preferences[:2]),
            playbook=("Check this before sending anything from the actions above.",),
        )
    ]


def confirm_conflicts(context: Context) -> list[Recommendation]:
    """Two memories disagree and nobody has said which is true."""
    conflicted = [
        memory
        for memory in context.memories
        if bool((memory.attributes or {}).get("conflict"))
    ]
    if not conflicted:
        return []
    return [
        Recommendation(
            key="confirm_conflict",
            action=f"Confirm which is current: {_clip(conflicted[0].content)}",
            quotes=((conflicted[0].id, _clip(conflicted[0].content)),),
            rationale="Two memories disagree, so answers about this may be wrong either way.",
            category="data_quality",
            urgency=0.4,
            memory_ids=tuple(memory.id for memory in conflicted[:3]),
            playbook=(
                "Ask the customer, or check the system of record.",
                "Confirm or correct the memory so the conflict resolves itself.",
            ),
        )
    ]


RULES: tuple[Rule, ...] = (
    resolve_open_problems,
    escalate_repeat_problems,
    retention_outreach,
    re_engage,
    unblock_goals,
    expansion_offer,
    ask_for_advocacy,
    close_the_loop,
    confirm_conflicts,
    respect_contact_preference,
)


def recommend(
    *,
    memories: Sequence[MemoryView],
    report: SignalReport,
    goals: Sequence[GoalSnapshot] = (),
    health_score: float = 70.0,
    customer_name: str | None = None,
    limit: int = MAX_RECOMMENDATIONS,
    now: datetime | None = None,
) -> list[Recommendation]:
    """Run every rule and return the strongest few, most urgent first."""
    context = Context(
        memories=memories,
        report=report,
        goals=goals,
        health_score=health_score,
        customer_name=customer_name,
        now=now or utcnow(),
    )

    produced: list[Recommendation] = []
    for rule in RULES:
        produced.extend(rule(context))

    # Ties break on key so the same inputs always produce the same order.
    produced.sort(key=lambda item: (-item.urgency, item.key))
    return produced[:limit]


def summarise(recommendations: Sequence[Recommendation]) -> str:
    """One line for a list cell: the first action, and how much else is waiting."""
    if not recommendations:
        return "Nothing needs doing."
    first = recommendations[0]
    if len(recommendations) == 1:
        return first.action
    return f"{first.action} (+{len(recommendations) - 1} more)"


def directions(report: SignalReport) -> dict[str, int]:
    """Counts by direction, for the portfolio header."""
    return {
        "risk": sum(1 for s in report.signals if s.direction == SignalDirection.RISK),
        "opportunity": sum(
            1 for s in report.signals if s.direction == SignalDirection.OPPORTUNITY
        ),
    }
