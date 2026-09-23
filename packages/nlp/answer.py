"""Deterministic answer composition.

This is the module that replaces "ask an LLM". An answer is assembled from memories the
retrieval engine actually returned: every clause is either a stored sentence, a count, a
date, or a fixed connective. Nothing is generated from a model's priors, so the system
cannot state something the customer's history does not support — and the same question
over the same memories always produces the same answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from common.enums import MemoryType
from common.time import days_between, ensure_utc, utcnow
from nlp.question import QuestionAnalysis, QuestionIntent
from nlp.summarize import mmr_select
from nlp.tokenize import content_words, lemmatize

# How far back to look for causes of an event ("why did they downgrade?").
CAUSAL_WINDOW_DAYS = 45
MAX_LISTED = 4

_NOT_ENOUGH = "There is not enough memory about this customer to answer that."


@dataclass(slots=True)
class MemoryView:
    """The subset of a memory the composer needs. Engine-agnostic by design."""

    id: str
    type: MemoryType
    content: str
    importance: float = 0.5
    confidence: float = 0.5
    last_seen_at: datetime = field(default_factory=utcnow)
    first_seen_at: datetime = field(default_factory=utcnow)
    evidence_count: int = 1
    source_event_ids: list[str] = field(default_factory=list)
    score: float = 0.0
    entities: list[str] = field(default_factory=list)
    status: str = "active"
    attributes: dict[str, object] = field(default_factory=dict)

    @property
    def is_resolved(self) -> bool:
        return bool(self.attributes.get("resolved"))

    @property
    def urgency(self) -> float:
        sentiment = self.attributes.get("sentiment")
        if isinstance(sentiment, dict):
            return float(sentiment.get("urgency", 0.0) or 0.0)
        return 0.0

    @property
    def churn_risk(self) -> float:
        sentiment = self.attributes.get("sentiment")
        if isinstance(sentiment, dict):
            return float(sentiment.get("churn_risk", 0.0) or 0.0)
        return 0.0


@dataclass(slots=True)
class EventView:
    id: str
    event_type: str
    occurred_at: datetime
    importance: float = 0.0


@dataclass(slots=True)
class Answer:
    text: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    strategy: str = "generic"
    reasoning: list[str] = field(default_factory=list)
    facts: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "answer": self.text,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "event_ids": self.event_ids,
            "strategy": self.strategy,
            "reasoning": self.reasoning,
            "facts": self.facts,
        }


# ------------------------------------------------------------------ formatting

def format_date(value: datetime) -> str:
    return ensure_utc(value).strftime("%d %b %Y")


def relative_days(value: datetime, *, now: datetime | None = None) -> str:
    days = days_between(value, now or utcnow())
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{int(days)} days ago"
    if days < 60:
        return f"{int(days // 7)} weeks ago"
    if days < 365:
        return f"{int(days // 30)} months ago"
    return f"{days / 365:.1f} years ago"


def join_clauses(items: Sequence[str]) -> str:
    items = [item for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def count_phrase(count: int, noun: str, plural: str | None = None) -> str:
    plural = plural or f"{noun}s"
    if count == 1:
        return f"one {noun}"
    if count == 2:
        return f"two {plural}"
    return f"{count} {plural}"


def _sentence(text: str) -> str:
    """Capitalise and terminate a fragment so composed answers read as prose."""
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _strip_subject(content: str) -> str:
    """"The customer cannot connect Shopify." → "cannot connect Shopify"."""
    lowered = content.strip()
    for prefix in ("The customer's ", "The customer ", "Customer said: ", "Customer "):
        if lowered.startswith(prefix):
            remainder = lowered[len(prefix) :]
            if prefix == "The customer's ":
                remainder = "their " + remainder
            return remainder.rstrip(".")
    return lowered.rstrip(".")


# ------------------------------------------------------------------- selection

def _of_type(memories: Sequence[MemoryView], *types: MemoryType) -> list[MemoryView]:
    wanted = {memory_type.value for memory_type in types}
    return [memory for memory in memories if str(memory.type) in wanted]


def _by_recency(memories: Sequence[MemoryView]) -> list[MemoryView]:
    return sorted(memories, key=lambda memory: ensure_utc(memory.last_seen_at), reverse=True)


def _by_priority(memories: Sequence[MemoryView]) -> list[MemoryView]:
    return sorted(
        memories,
        key=lambda memory: (memory.score, memory.importance, ensure_utc(memory.last_seen_at).timestamp()),
        reverse=True,
    )


def _unique(memories: Sequence[MemoryView]) -> list[MemoryView]:
    """Drop memories whose wording is identical; an answer must not repeat itself."""
    seen: set[str] = set()
    kept: list[MemoryView] = []
    for memory in memories:
        key = " ".join(content_words(memory.content))
        if key in seen:
            continue
        seen.add(key)
        kept.append(memory)
    return kept


def _diverse(memories: Sequence[MemoryView], limit: int) -> list[MemoryView]:
    selected = mmr_select(
        _unique(memories),
        text_of=lambda memory: memory.content,  # type: ignore[attr-defined]
        score_of=lambda memory: max(memory.score, memory.importance),  # type: ignore[attr-defined]
        limit=limit,
    )
    return list(selected)  # type: ignore[arg-type]


def _entities_of(memories: Sequence[MemoryView]) -> list[str]:
    counter: Counter[str] = Counter()
    for memory in memories:
        counter.update(memory.entities)
    return [name for name, _ in counter.most_common(3)]


def _confidence(
    *,
    base: float,
    memories: Sequence[MemoryView],
    required: int = 2,
) -> float:
    if not memories:
        return 0.15
    top_score = max((memory.score for memory in memories), default=0.0)
    average_confidence = sum(memory.confidence for memory in memories) / len(memories)
    coverage = min(1.0, len(memories) / max(1, required))
    evidence = min(1.0, sum(memory.evidence_count for memory in memories) / (required * 2))
    value = base + 0.2 * top_score + 0.2 * average_confidence + 0.15 * coverage + 0.1 * evidence
    return round(max(0.1, min(0.97, value)), 4)


# ------------------------------------------------------------------ strategies

def _answer_why(analysis: QuestionAnalysis, memories: Sequence[MemoryView], events: Sequence[EventView]) -> Answer:
    target = analysis.causal_target or ""
    target_words = {
        "downgrade": ("downgrade", "downgraded"),
        "cancel": ("cancel", "cancelled", "canceled", "cancellation"),
        "upgrade": ("upgrade", "upgraded"),
        "refund": ("refund", "refunded"),
        "complaint": ("complain", "complaint", "escalate"),
        "contact_support": ("support", "ticket"),
    }.get(target, ())

    trigger = None
    if target_words:
        # Compare lemmas on both sides: "downgraded" and "downgrade" are the same event.
        wanted = {lemmatize(word) for word in target_words}
        for memory in _by_recency(memories):
            if wanted & set(content_words(memory.content)):
                trigger = memory
                break

    if trigger is None:
        # No record of the thing being asked about: say so rather than guessing a cause.
        problems = _by_priority(_of_type(memories, MemoryType.PROBLEM, MemoryType.FEEDBACK))
        if not problems:
            return Answer(text=_NOT_ENOUGH, confidence=0.15, strategy="why:no_trigger")
        listed = _diverse(problems, MAX_LISTED - 1)
        subject = f"a {target}" if target else "that"
        text = (
            f"There is no memory of {subject} for this customer. "
            f"The most significant recorded issues are: "
            + join_clauses([_strip_subject(memory.content) for memory in listed])
            + "."
        )
        return Answer(
            text=text,
            confidence=_confidence(base=0.25, memories=listed),
            evidence=[memory.id for memory in listed],
            event_ids=sorted({event_id for memory in listed for event_id in memory.source_event_ids}),
            strategy="why:no_trigger",
            reasoning=[f"No memory matched the causal target '{target or 'unspecified'}'."],
        )

    trigger_time = ensure_utc(trigger.last_seen_at)
    window_start = trigger_time - timedelta(days=CAUSAL_WINDOW_DAYS)
    contributors = [
        memory
        for memory in memories
        if memory.id != trigger.id
        and str(memory.type) in {MemoryType.PROBLEM.value, MemoryType.FEEDBACK.value, MemoryType.INTENT.value}
        and window_start <= ensure_utc(memory.last_seen_at) <= trigger_time + timedelta(days=1)
    ]
    contributors = _by_priority(contributors)
    listed = _diverse(contributors, MAX_LISTED)

    parts = [_sentence(f"{trigger.content.rstrip('.')} ({relative_days(trigger_time)})")]
    reasoning = [f"Trigger memory {trigger.id} matched the causal target '{target}'."]

    if listed:
        entities = _entities_of(listed)
        focus = f" involving {join_clauses(entities)}" if entities else ""
        parts.append(
            _sentence(
                f"In the {CAUSAL_WINDOW_DAYS} days before that, the memory holds "
                f"{count_phrase(len(contributors), 'related issue')}{focus}"
            )
        )
        parts.append(
            _sentence(
                "The most relevant are: "
                + join_clauses([_strip_subject(memory.content) for memory in listed])
            )
        )
        reasoning.append(
            f"{len(contributors)} problem/feedback/intent memories fall in the {CAUSAL_WINDOW_DAYS}-day window before it."
        )
    else:
        parts.append(
            _sentence(
                "No problems, feedback or intent signals are recorded in the "
                f"{CAUSAL_WINDOW_DAYS} days before it, so the memory does not explain why"
            )
        )
        reasoning.append("No contributing memories in the causal window.")

    if "citing:" in trigger.content:
        reasoning.append("The trigger memory carries an explicit reason from the event payload.")

    evidence = [trigger.id, *[memory.id for memory in listed]]
    return Answer(
        text=" ".join(parts),
        confidence=_confidence(base=0.35 if listed else 0.25, memories=[trigger, *listed], required=3),
        evidence=evidence,
        event_ids=sorted({event_id for memory in [trigger, *listed] for event_id in memory.source_event_ids}),
        strategy="why:causal_window",
        reasoning=reasoning,
        facts={
            "trigger_memory_id": trigger.id,
            "trigger_at": trigger_time.isoformat(),
            "contributing_memories": len(contributors),
            "window_days": CAUSAL_WINDOW_DAYS,
        },
    )


def _answer_problems(analysis: QuestionAnalysis, memories: Sequence[MemoryView]) -> Answer:
    problems = [memory for memory in _of_type(memories, MemoryType.PROBLEM) if not memory.is_resolved]
    resolved = [memory for memory in _of_type(memories, MemoryType.PROBLEM) if memory.is_resolved]
    if not problems:
        if resolved:
            listed = _diverse(resolved, 2)
            return Answer(
                text=_sentence(
                    "No open problems are recorded. Previously resolved: "
                    + join_clauses([_strip_subject(memory.content) for memory in listed])
                ),
                confidence=_confidence(base=0.3, memories=listed),
                evidence=[memory.id for memory in listed],
                strategy="problems:resolved_only",
            )
        return Answer(text="No problems are recorded for this customer.", confidence=0.4,
                      strategy="problems:none")

    ranked = _by_priority(problems)
    listed = _diverse(ranked, MAX_LISTED)
    entities = _entities_of(ranked)
    latest = ensure_utc(ranked[0].last_seen_at)
    repeated = [memory for memory in ranked if memory.evidence_count > 1]

    parts = [
        _sentence(
            f"{count_phrase(len(problems), 'open problem')} "
            + (f"involving {join_clauses(entities)} " if entities else "")
            + f"{'are' if len(problems) > 1 else 'is'} recorded, most recently {relative_days(latest)}"
        ),
        _sentence(join_clauses([_strip_subject(memory.content) for memory in listed])),
    ]
    if repeated:
        worst = max(repeated, key=lambda memory: memory.evidence_count)
        parts.append(
            _sentence(
                f"The most persistent has been reported {worst.evidence_count} times: "
                f"{_strip_subject(worst.content)}"
            )
        )
    urgent = [memory for memory in ranked if memory.urgency >= 0.6]
    if urgent:
        parts.append(_sentence(f"{count_phrase(len(urgent), 'report')} used urgent language"))

    return Answer(
        text=" ".join(parts),
        confidence=_confidence(base=0.4, memories=listed, required=2),
        evidence=[memory.id for memory in listed],
        event_ids=sorted({event_id for memory in listed for event_id in memory.source_event_ids}),
        strategy="problems:list",
        reasoning=[f"{len(problems)} open and {len(resolved)} resolved problem memories retrieved."],
        facts={"open_problems": len(problems), "resolved_problems": len(resolved), "entities": entities},
    )


def _answer_simple_list(
    memories: Sequence[MemoryView],
    *,
    types: Sequence[MemoryType],
    empty_text: str,
    lead: str,
    strategy: str,
) -> Answer:
    selected = _by_priority(_of_type(memories, *types))
    if not selected:
        return Answer(text=empty_text, confidence=0.35, strategy=f"{strategy}:none")
    listed = _diverse(selected, MAX_LISTED)
    text = _sentence(lead + " " + join_clauses([_strip_subject(memory.content) for memory in listed]))
    return Answer(
        text=text,
        confidence=_confidence(base=0.4, memories=listed),
        evidence=[memory.id for memory in listed],
        event_ids=sorted({event_id for memory in listed for event_id in memory.source_event_ids}),
        strategy=strategy,
    )


def _answer_when(analysis: QuestionAnalysis, memories: Sequence[MemoryView]) -> Answer:
    target_words = set(analysis.keywords)
    matches = [
        memory
        for memory in memories
        if target_words & set(content_words(memory.content))
    ] or list(memories)
    if not matches:
        return Answer(text=_NOT_ENOUGH, confidence=0.15, strategy="when:none")
    memory = _by_priority(matches)[0]
    first = ensure_utc(memory.first_seen_at)
    last = ensure_utc(memory.last_seen_at)
    if abs((last - first).total_seconds()) < 86400:
        text = _sentence(f"{memory.content.rstrip('.')} — recorded {relative_days(last)} ({last:%d %b %Y})")
    else:
        text = _sentence(
            f"{memory.content.rstrip('.')} — first recorded {relative_days(first)} "
            f"({first:%d %b %Y}) and last confirmed {relative_days(last)} ({last:%d %b %Y})"
        )
    return Answer(
        text=text,
        confidence=_confidence(base=0.4, memories=[memory], required=1),
        evidence=[memory.id],
        event_ids=list(memory.source_event_ids),
        strategy="when:memory_dates",
        facts={"first_seen_at": first.isoformat(), "last_seen_at": last.isoformat()},
    )


def _answer_how_many(
    analysis: QuestionAnalysis, memories: Sequence[MemoryView], events: Sequence[EventView]
) -> Answer:
    keywords = set(analysis.keywords) - {"many", "times", "often", "customer"}
    matching = [
        memory for memory in memories if not keywords or keywords & set(content_words(memory.content))
    ]
    occurrences = sum(max(1, memory.evidence_count) for memory in matching)
    window = ""
    if analysis.since is not None:
        matching = [memory for memory in matching if ensure_utc(memory.last_seen_at) >= analysis.since]
        occurrences = sum(max(1, memory.evidence_count) for memory in matching)
        window = " in the period asked about"
    if not matching:
        return Answer(text="Nothing matching that is recorded for this customer.", confidence=0.35,
                      strategy="how_many:none")

    subject = join_clauses(sorted({name for memory in matching for name in memory.entities})[:3]) or "matching events"
    text = _sentence(
        f"{occurrences} recorded occurrence{'s' if occurrences != 1 else ''}{window}, "
        f"across {count_phrase(len(matching), 'memory', 'memories')} about {subject}"
    )
    listed = _diverse(matching, 3)
    return Answer(
        text=text + " " + _sentence(join_clauses([_strip_subject(memory.content) for memory in listed])),
        confidence=_confidence(base=0.4, memories=matching),
        evidence=[memory.id for memory in matching][:10],
        event_ids=sorted({event_id for memory in matching for event_id in memory.source_event_ids})[:20],
        strategy="how_many:count",
        facts={"occurrences": occurrences, "memories": len(matching)},
    )


def _answer_risk(analysis: QuestionAnalysis, memories: Sequence[MemoryView]) -> Answer:
    signals: list[str] = []
    score = 0.0

    churn_memories = [memory for memory in memories if memory.churn_risk >= 0.4]
    if churn_memories:
        score += 0.35
        signals.append(f"{count_phrase(len(churn_memories), 'memory', 'memories')} mention leaving or cancelling")

    open_problems = [
        memory for memory in _of_type(memories, MemoryType.PROBLEM) if not memory.is_resolved
    ]
    if open_problems:
        score += min(0.3, 0.1 * len(open_problems))
        signals.append(f"{count_phrase(len(open_problems), 'open problem')}")

    repeated = [memory for memory in open_problems if memory.evidence_count >= 2]
    if repeated:
        score += 0.15
        signals.append(f"{count_phrase(len(repeated), 'problem')} reported more than once")

    churn_lemmas = {lemmatize(word) for word in ("downgrade", "downgraded", "cancel", "cancelled", "cancellation")}
    downgrades = [
        memory
        for memory in _of_type(memories, MemoryType.SUBSCRIPTION, MemoryType.INTENT)
        if churn_lemmas & set(content_words(memory.content))
    ]
    if downgrades:
        score += 0.25
        signals.append("a downgrade or cancellation is recorded")

    negative_feedback = [
        memory for memory in _of_type(memories, MemoryType.FEEDBACK) if memory.importance >= 0.7
    ]
    if negative_feedback:
        score += 0.1
        signals.append("negative feedback is recorded")

    recent = [
        memory for memory in memories if days_between(memory.last_seen_at, utcnow()) <= 30
    ]
    if not recent:
        signals.append("no activity in the last 30 days")
        score += 0.05

    score = round(min(1.0, score), 3)
    band = "high" if score >= 0.6 else "moderate" if score >= 0.3 else "low"
    if not signals:
        return Answer(
            text="Risk appears low: no churn signals, open problems or negative feedback are recorded.",
            confidence=0.45,
            strategy="risk:none",
            facts={"risk_score": 0.0, "band": "low"},
        )

    relevant = _by_priority([*churn_memories, *open_problems, *downgrades])[:MAX_LISTED]
    text = _sentence(f"Churn risk looks {band} ({score:.2f}): " + join_clauses(signals))
    if relevant:
        text += " " + _sentence(
            "Supporting memories: " + join_clauses([_strip_subject(memory.content) for memory in relevant])
        )
    return Answer(
        text=text,
        confidence=_confidence(base=0.35, memories=relevant or list(memories)),
        evidence=[memory.id for memory in relevant],
        event_ids=sorted({event_id for memory in relevant for event_id in memory.source_event_ids}),
        strategy="risk:scored",
        reasoning=list(signals),  # copy: the caller prepends to reasoning
        facts={"risk_score": score, "band": band, "signals": signals},
    )


def _answer_status(analysis: QuestionAnalysis, memories: Sequence[MemoryView]) -> Answer:
    topic = set(analysis.keywords) | {name.lower() for name in analysis.entity_names}
    relevant = [
        memory for memory in memories
        if not topic or topic & {word.lower() for word in content_words(memory.content)}
        or topic & {name.lower() for name in memory.entities}
    ]
    if not relevant:
        return Answer(text=_NOT_ENOUGH, confidence=0.15, strategy="status:none")

    ordered = _by_recency(relevant)
    latest = ordered[0]
    resolved = latest.is_resolved or "resolved" in latest.content.lower()
    open_problems = [
        memory for memory in _of_type(ordered, MemoryType.PROBLEM) if not memory.is_resolved
    ]

    if resolved and not open_problems:
        headline = "The latest memory says this is resolved."
    elif open_problems:
        headline = (
            f"Still open: {count_phrase(len(open_problems), 'problem')} "
            f"recorded, most recently {relative_days(ensure_utc(open_problems[0].last_seen_at))}."
        )
    else:
        headline = f"Latest recorded state ({relative_days(ensure_utc(latest.last_seen_at))}):"

    listed = _diverse(ordered, 3)
    return Answer(
        text=_sentence(headline) + " " + _sentence(
            join_clauses([_strip_subject(memory.content) for memory in listed])
        ),
        confidence=_confidence(base=0.4, memories=listed),
        evidence=[memory.id for memory in listed],
        event_ids=sorted({event_id for memory in listed for event_id in memory.source_event_ids}),
        strategy="status:latest",
        facts={"resolved": resolved, "open_problems": len(open_problems)},
    )


def _answer_history(memories: Sequence[MemoryView], events: Sequence[EventView]) -> Answer:
    ordered = _by_recency(memories)[:6]
    if not ordered:
        return Answer(text=_NOT_ENOUGH, confidence=0.15, strategy="history:none")
    lines = [
        f"{ensure_utc(memory.last_seen_at):%d %b}: {_strip_subject(memory.content)}"
        for memory in ordered
    ]
    text = "Most recent first — " + "; ".join(lines) + "."
    return Answer(
        text=text,
        confidence=_confidence(base=0.4, memories=ordered),
        evidence=[memory.id for memory in ordered],
        event_ids=sorted({event_id for memory in ordered for event_id in memory.source_event_ids}),
        strategy="history:timeline",
        facts={"events_seen": len(events)},
    )


def _answer_summary(memories: Sequence[MemoryView]) -> Answer:
    if not memories:
        return Answer(text=_NOT_ENOUGH, confidence=0.15, strategy="summary:none")
    sections: list[str] = []
    evidence: list[str] = []

    for label, types in (
        ("Open problems", (MemoryType.PROBLEM,)),
        ("Preferences", (MemoryType.PREFERENCE,)),
        ("Goals", (MemoryType.GOAL, MemoryType.INTENT)),
        ("Subscription", (MemoryType.SUBSCRIPTION,)),
        ("Product usage", (MemoryType.BEHAVIOR,)),
        ("Other facts", (MemoryType.FACT, MemoryType.RELATIONSHIP, MemoryType.FEEDBACK)),
    ):
        selected = _diverse(_by_priority(_of_type(memories, *types)), 2)
        if not selected:
            continue
        sections.append(
            f"{label}: " + join_clauses([_strip_subject(memory.content) for memory in selected])
        )
        evidence.extend(memory.id for memory in selected)

    text = ". ".join(sections) + "." if sections else _NOT_ENOUGH
    return Answer(
        text=text,
        confidence=_confidence(base=0.4, memories=memories, required=4),
        evidence=evidence,
        event_ids=sorted({event_id for memory in memories for event_id in memory.source_event_ids})[:20],
        strategy="summary:sections",
    )


def _answer_generic(analysis: QuestionAnalysis, memories: Sequence[MemoryView]) -> Answer:
    if not memories:
        return Answer(text=_NOT_ENOUGH, confidence=0.15, strategy="generic:none")
    listed = _diverse(_by_priority(memories), MAX_LISTED)
    text = _sentence(
        "Based on "
        + count_phrase(len(memories), "relevant memory", "relevant memories")
        + ": "
        + join_clauses([_strip_subject(memory.content) for memory in listed])
    )
    return Answer(
        text=text,
        confidence=_confidence(base=0.32, memories=listed),
        evidence=[memory.id for memory in listed],
        event_ids=sorted({event_id for memory in listed for event_id in memory.source_event_ids}),
        strategy="generic:extractive",
    )


# --------------------------------------------------------------------- compose

def compose(
    *,
    analysis: QuestionAnalysis,
    memories: Sequence[MemoryView],
    events: Sequence[EventView] = (),
    customer_name: str | None = None,
) -> Answer:
    """Answer a question from retrieved memories, deterministically."""
    active = [memory for memory in memories if memory.status in ("active", "")]
    pool = active or list(memories)

    if analysis.intent is QuestionIntent.WHY:
        answer = _answer_why(analysis, pool, events)
    elif analysis.intent is QuestionIntent.PROBLEMS:
        answer = _answer_problems(analysis, pool)
    elif analysis.intent is QuestionIntent.PREFERENCES:
        answer = _answer_simple_list(
            pool,
            types=(MemoryType.PREFERENCE,),
            empty_text="No contact or product preferences are recorded for this customer.",
            lead="Recorded preferences:",
            strategy="preferences:list",
        )
    elif analysis.intent is QuestionIntent.GOALS:
        answer = _answer_simple_list(
            pool,
            types=(MemoryType.GOAL, MemoryType.INTENT),
            empty_text="No goals or stated intentions are recorded for this customer.",
            lead="Recorded goals and intentions:",
            strategy="goals:list",
        )
    elif analysis.intent is QuestionIntent.WHO:
        answer = _answer_simple_list(
            pool,
            types=(MemoryType.RELATIONSHIP, MemoryType.FACT),
            empty_text="No people or companies are recorded for this customer.",
            lead="Recorded relationships:",
            strategy="who:list",
        )
    elif analysis.intent is QuestionIntent.WHEN:
        answer = _answer_when(analysis, pool)
    elif analysis.intent is QuestionIntent.HOW_MANY:
        answer = _answer_how_many(analysis, pool, events)
    elif analysis.intent is QuestionIntent.RISK:
        answer = _answer_risk(analysis, pool)
    elif analysis.intent is QuestionIntent.STATUS:
        answer = _answer_status(analysis, pool)
    elif analysis.intent is QuestionIntent.HISTORY:
        answer = _answer_history(pool, events)
    elif analysis.intent is QuestionIntent.SUMMARY:
        answer = _answer_summary(pool)
    else:
        answer = _answer_generic(analysis, pool)

    if customer_name and answer.text != _NOT_ENOUGH:
        answer.text = answer.text.replace("The customer ", f"{customer_name} ", 1)
        answer.text = answer.text.replace("the customer", customer_name)
    answer.reasoning.insert(0, f"Question intent: {analysis.intent.value}.")
    return answer
