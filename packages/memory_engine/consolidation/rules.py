"""The consolidation decision, as a pure function.

Given one existing memory and one new candidate, decide what should happen: merge, update,
conflict, create or ignore. Keeping this pure (no database, no I/O) is what makes memory
evolution testable — every branch below has a test, and an operator can replay any decision
from the signals stored on the memory version.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.enums import ConsolidationAction, MemoryType
from common.text import content_hash, jaccard
from common.time import days_between, ensure_utc
from nlp.entities import channels_mentioned
from nlp.lexicon import PLANS
from nlp.sentiment import analyze as analyze_sentiment
from nlp.tokenize import content_words, lemmatize

# Blended similarity above which two statements are the same statement.
AUTO_MERGE_SIMILARITY = 0.82
# Below this, two statements are about different things and both are kept.
DEFAULT_THRESHOLD = 0.45
# A merge also requires real lexical agreement, not just a close vector.
MIN_TOPIC_OVERLAP = 0.12
# New content words needed before an update is worth a new version row.
NOVELTY_RATIO = 0.3
# Reports within this window count towards a recurrence note.
RECURRENCE_WINDOW_DAYS = 45
RECURRENCE_MIN_REPORTS = 3

MAX_CONTENT_LENGTH = 900


@dataclass(slots=True)
class MemorySnapshot:
    """The parts of a stored memory the rules need."""

    id: str
    type: MemoryType
    content: str
    confidence: float
    importance: float
    evidence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    source: str = "event"
    entities: Sequence[str] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateSnapshot:
    type: MemoryType
    content: str
    confidence: float
    importance: float
    occurred_at: datetime
    entities: Sequence[str] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Decision:
    action: ConsolidationAction
    content: str
    confidence: float
    reason: str
    similarity: float
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def creates_new_memory(self) -> bool:
        return self.action in (ConsolidationAction.CREATE, ConsolidationAction.CONFLICT)


def topic_overlap(left: str, right: str) -> float:
    return jaccard(left, right)


def shared_entities(left: Sequence[str], right: Sequence[str]) -> list[str]:
    lowered = {name.lower() for name in right}
    return sorted({name for name in left if name.lower() in lowered})


def is_exact_duplicate(left: str, right: str) -> bool:
    return content_hash(left) == content_hash(right)


def _plans_in(text: str) -> set[str]:
    words = set(content_words(text))
    return {display for key, display in PLANS.items() if key in words}


_TRANSITION_MARKERS = ("upgrade", "downgrade", "moved", "switched", "changed", "from", "cancel")
_CURRENT_PLAN_RE = re.compile(r"\bto the ([A-Za-z]+) plan\b", re.I)


def is_transition(text: str) -> bool:
    """Does this statement describe a change ("upgraded from X to Y") rather than a state?"""
    words = set(content_words(text))
    return any(lemmatize(marker) in words for marker in _TRANSITION_MARKERS)


def current_plan(text: str) -> str | None:
    """The plan a statement leaves the customer on, if it names one."""
    match = _CURRENT_PLAN_RE.search(text)
    if match:
        return PLANS.get(match.group(1).lower(), match.group(1).title())
    plans = _plans_in(text)
    return next(iter(plans)) if len(plans) == 1 else None


def detect_contradiction(existing: MemorySnapshot, candidate: CandidateSnapshot) -> tuple[bool, str]:
    """Do these two statements disagree about the same thing?"""
    overlap = topic_overlap(existing.content, candidate.content)

    # 1. A stated preference moved to a different channel.
    if existing.type == MemoryType.PREFERENCE and candidate.type == MemoryType.PREFERENCE:
        existing_channels = set(channels_mentioned(existing.content))
        candidate_channels = set(channels_mentioned(candidate.content))
        if existing_channels and candidate_channels and existing_channels != candidate_channels:
            return True, (
                f"contact channel changed from {', '.join(sorted(existing_channels))} "
                f"to {', '.join(sorted(candidate_channels))}"
            )

    # 2. A *state* statement names a different current plan. Transitions ("upgraded from
    #    Starter to Pro") are historical facts and never contradict each other.
    if (
        existing.type == MemoryType.SUBSCRIPTION
        and candidate.type == MemoryType.SUBSCRIPTION
        and not is_transition(existing.content)
        and not is_transition(candidate.content)
    ):
        existing_plan = current_plan(existing.content)
        candidate_plan = current_plan(candidate.content)
        if existing_plan and candidate_plan and existing_plan != candidate_plan:
            return True, f"current plan changed from {existing_plan} to {candidate_plan}"

    # 3. A problem is now reported as resolved.
    if (
        existing.type == MemoryType.PROBLEM
        and candidate.attributes.get("resolved")
        and (overlap >= 0.2 or shared_entities(existing.entities, candidate.entities))
    ):
        return True, "the same issue is now reported as resolved"

    # 4. Same subject, opposite polarity ("X works" vs "X does not work").
    if overlap >= 0.45:
        existing_polarity = analyze_sentiment(existing.content).polarity
        candidate_polarity = analyze_sentiment(candidate.content).polarity
        if existing_polarity * candidate_polarity < 0 and abs(existing_polarity - candidate_polarity) >= 0.5:
            return True, "the statements assert opposite things about the same subject"

    return False, ""


def novelty(existing: str, candidate: str) -> tuple[float, list[str]]:
    """Share of the candidate's meaningful words that the existing memory lacks."""
    existing_words = set(content_words(existing))
    candidate_words = [word for word in content_words(candidate) if word not in existing_words]
    if not content_words(candidate):
        return 0.0, []
    return len(candidate_words) / len(content_words(candidate)), sorted(set(candidate_words))


def recurrence_note(existing: MemorySnapshot, candidate: CandidateSnapshot) -> str | None:
    """"Reported 4 times since 1 Sep" — a fact derived from counts, not a judgement."""
    reports = existing.evidence_count + 1
    if reports < RECURRENCE_MIN_REPORTS:
        return None
    span = days_between(existing.first_seen_at, candidate.occurred_at)
    if span > RECURRENCE_WINDOW_DAYS:
        return None
    since = ensure_utc(existing.first_seen_at).strftime("%d %b %Y")
    return f"Reported {reports} times since {since}."


def merge_content(existing: MemorySnapshot, candidate: CandidateSnapshot, note: str | None) -> str:
    """Produce the single best current statement, without inventing anything.

    A memory's content is not an append-only log — that is what ``memory_versions`` is for.
    Concatenating every restatement makes memories grow without bound and read badly in a
    context window, so the more informative sentence wins and the other is preserved as a
    version. The only thing added is a factual recurrence note derived from counts.
    """
    existing_words = set(content_words(existing.content))
    candidate_words = set(content_words(candidate.content))

    if existing_words and existing_words <= candidate_words:
        base = candidate.content  # strictly more specific
    elif candidate_words and candidate_words <= existing_words:
        base = existing.content  # nothing new to add
    elif len(candidate_words) > len(existing_words):
        base = candidate.content  # carries more detail
    elif len(candidate_words) < len(existing_words):
        base = existing.content
    else:
        base = candidate.content  # equally informative: prefer the newer wording

    base = base.rstrip()
    if note:
        base = f"{base.rstrip('.')}. {note}"
    return base[:MAX_CONTENT_LENGTH]


def decide(
    *,
    existing: MemorySnapshot,
    candidate: CandidateSnapshot,
    similarity: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> Decision:
    """Decide how a candidate memory relates to the closest existing memory."""
    overlap = topic_overlap(existing.content, candidate.content)
    common_entities = shared_entities(existing.entities, candidate.entities)
    signals: dict[str, Any] = {
        "similarity": round(similarity, 4),
        "lexical_overlap": round(overlap, 4),
        "shared_entities": common_entities,
        "existing_evidence": existing.evidence_count,
        "same_type": existing.type == candidate.type,
    }

    if is_exact_duplicate(existing.content, candidate.content):
        return Decision(
            action=ConsolidationAction.MERGE,
            content=existing.content,
            confidence=min(0.99, existing.confidence + 0.02),
            reason="Identical statement already recorded.",
            similarity=1.0,
            signals={**signals, "rule": "exact_duplicate"},
        )

    # Two different subscription transitions are two things that happened: keep both.
    if (
        existing.type == MemoryType.SUBSCRIPTION
        and candidate.type == MemoryType.SUBSCRIPTION
        and (is_transition(existing.content) or is_transition(candidate.content))
        and current_plan(existing.content) != current_plan(candidate.content)
    ):
        return Decision(
            action=ConsolidationAction.CREATE,
            content=candidate.content,
            confidence=candidate.confidence,
            reason="A subscription change is a separate historical event.",
            similarity=similarity,
            signals={**signals, "rule": "distinct_transition"},
        )

    contradicts, contradiction_reason = detect_contradiction(existing, candidate)
    if contradicts:
        return Decision(
            action=ConsolidationAction.CONFLICT,
            content=candidate.content,
            confidence=candidate.confidence,
            reason=f"Contradicts the existing memory: {contradiction_reason}.",
            similarity=similarity,
            signals={**signals, "rule": "contradiction", "detail": contradiction_reason},
        )

    # A problem is not a fact and a preference is not a behaviour: different kinds of
    # statement stay separate memories even when they share vocabulary.
    if str(existing.type) != str(candidate.type):
        return Decision(
            action=ConsolidationAction.CREATE,
            content=candidate.content,
            confidence=candidate.confidence,
            reason="The statements are different kinds of memory.",
            similarity=similarity,
            signals={**signals, "rule": "type_mismatch"},
        )

    # Different subject, or the same words about different things: keep both.
    if similarity < threshold or (overlap < MIN_TOPIC_OVERLAP and not common_entities):
        return Decision(
            action=ConsolidationAction.CREATE,
            content=candidate.content,
            confidence=candidate.confidence,
            reason="No sufficiently similar memory exists.",
            similarity=similarity,
            signals={**signals, "rule": "below_threshold"},
        )

    note = recurrence_note(existing, candidate)
    novelty_ratio, new_words = novelty(existing.content, candidate.content)
    signals.update({"novelty": round(novelty_ratio, 3), "new_words": new_words[:8]})

    if similarity >= AUTO_MERGE_SIMILARITY and novelty_ratio < NOVELTY_RATIO:
        content = merge_content(existing, candidate, note) if note else existing.content
        return Decision(
            action=ConsolidationAction.MERGE,
            content=content,
            confidence=min(0.99, max(existing.confidence, candidate.confidence) + 0.02),
            reason="Restates an existing memory; recorded as additional evidence.",
            similarity=similarity,
            signals={**signals, "rule": "auto_merge"},
        )

    if novelty_ratio >= NOVELTY_RATIO or note:
        return Decision(
            action=ConsolidationAction.UPDATE,
            content=merge_content(existing, candidate, note),
            confidence=min(0.99, max(existing.confidence, candidate.confidence)),
            reason=(
                "New evidence extends the existing memory"
                + (f" ({note.rstrip('.')})" if note else "")
                + "."
            ),
            similarity=similarity,
            signals={**signals, "rule": "novel_evidence"},
        )

    return Decision(
        action=ConsolidationAction.MERGE,
        content=existing.content,
        confidence=min(0.99, max(existing.confidence, candidate.confidence) + 0.01),
        reason="Same statement in different words.",
        similarity=similarity,
        signals={**signals, "rule": "paraphrase"},
    )
