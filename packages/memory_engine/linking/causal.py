"""Causal and temporal links between memories.

Events arrive as a flat stream; a customer's story is not flat. This module derives the
structure — which problems preceded a downgrade, which report resolved which issue, which
memories are about the same thing — using time, type, entity overlap and wording only.

Every link carries a rationale, and none is asserted as certain: a link says "these two
memories are related in this way", not "this caused that".

**Cost.** Inference is windowed, not quadratic. Memories are sorted by time once, and each
pass looks back only as far as its own window reaches — found by binary search, then capped
at ``MAX_LOOKBACK`` candidates so a burst of a thousand memories in one afternoon cannot
turn into a million comparisons. The work is therefore linear in the number of memories and
bounded per memory, which is what lets the caller hand it a customer's whole history rather
than the most recent handful.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from common.enums import MemoryType
from common.time import days_between, ensure_utc
from nlp.answer import MemoryView
from nlp.tokenize import content_words, lemmatize

# How long before an outcome a contributing memory may have occurred.
CAUSAL_WINDOW_DAYS = 45
# How far back a resolution statement may reach to close a problem. Generous, because a
# long-running problem can be fixed months later — but not unbounded, because a message
# today did not resolve something from two years ago.
RESOLUTION_WINDOW_DAYS = 180
# Memories closer than this in time and sharing an entity are considered related.
RELATED_WINDOW_DAYS = 7
MAX_LINKS_PER_MEMORY = 8
# The hard ceiling on candidates any one memory may be compared against, whatever the
# window says. This is what bounds a burst: a thousand memories in one afternoon all fall
# inside every window, and without this the pass would be quadratic again.
MAX_LOOKBACK = 120
# Memories close enough in sequence to be considered "about the same moment".
RELATED_LOOKBACK = 6


class LinkType(StrEnum):
    CAUSED_BY = "caused_by"
    RESOLVED_BY = "resolved_by"
    RELATES_TO = "relates_to"
    PRECEDED_BY = "preceded_by"


# Memory types that represent an outcome worth explaining.
OUTCOME_TYPES = {MemoryType.SUBSCRIPTION.value, MemoryType.INTENT.value}
CONTRIBUTING_TYPES = {
    MemoryType.PROBLEM.value,
    MemoryType.FEEDBACK.value,
    MemoryType.INTENT.value,
}

_OUTCOME_LEMMAS = {
    lemmatize(word)
    for word in ("downgrade", "cancel", "churn", "refund", "upgrade", "leave", "terminate")
}


@dataclass(slots=True, frozen=True)
class LinkProposal:
    source_memory_id: str
    target_memory_id: str
    link_type: LinkType
    confidence: float
    rationale: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source_memory_id, self.target_memory_id, self.link_type.value)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_memory_id": self.source_memory_id,
            "target_memory_id": self.target_memory_id,
            "link_type": self.link_type.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


def is_outcome(memory: MemoryView) -> bool:
    if str(memory.type) not in OUTCOME_TYPES:
        return False
    return bool(_OUTCOME_LEMMAS & set(content_words(memory.content)))


def _shared_entities(left: MemoryView, right: MemoryView) -> set[str]:
    return {name.lower() for name in left.entities} & {name.lower() for name in right.entities}


def _word_overlap(left: MemoryView, right: MemoryView) -> float:
    left_words = set(content_words(left.content))
    right_words = set(content_words(right.content))
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def _window_start(
    times: Sequence[datetime], index: int, *, moment: datetime, days: int
) -> int:
    """First index inside the look-back window, by binary search then capped by count.

    ``times`` is sorted, so the date bound is a bisect; the count bound then stops a dense
    burst from making the window enormous in practice.
    """
    earliest = bisect_left(times, moment - timedelta(days=days), 0, index)
    return max(earliest, index - MAX_LOOKBACK)


def infer(memories: Sequence[MemoryView]) -> list[LinkProposal]:
    """Derive links for one customer's memories."""
    ordered = sorted(memories, key=lambda memory: ensure_utc(memory.last_seen_at))
    times = [ensure_utc(memory.last_seen_at) for memory in ordered]
    proposals: dict[tuple[str, str, str], LinkProposal] = {}

    for index, memory in enumerate(ordered):
        # 1. Outcomes are explained by what happened in the window before them.
        if is_outcome(memory):
            outcome_time = times[index]
            start = _window_start(times, index, moment=outcome_time, days=CAUSAL_WINDOW_DAYS)
            contributors = [
                candidate
                for candidate in ordered[start:index]
                if str(candidate.type) in CONTRIBUTING_TYPES
            ]
            contributors.sort(key=lambda item: (item.importance, item.evidence_count), reverse=True)
            for candidate in contributors[:MAX_LINKS_PER_MEMORY]:
                age = days_between(candidate.last_seen_at, outcome_time)
                shared = _shared_entities(memory, candidate)
                # Closer in time and better evidenced means a stronger link.
                confidence = round(
                    min(
                        0.9,
                        0.35
                        + 0.35 * (1 - age / CAUSAL_WINDOW_DAYS)
                        + 0.05 * min(2, candidate.evidence_count),
                    ),
                    3,
                )
                proposal = LinkProposal(
                    source_memory_id=memory.id,
                    target_memory_id=candidate.id,
                    link_type=LinkType.CAUSED_BY,
                    confidence=confidence,
                    rationale=(
                        f"Recorded {int(age)} day(s) before this outcome"
                        + (f", sharing {', '.join(sorted(shared))}" if shared else "")
                        + "."
                    ),
                )
                proposals[proposal.key] = proposal

        # 2. A resolution statement resolves the problem it repeats.
        if memory.is_resolved or "resolved" in memory.content.lower():
            start = _window_start(
                times, index, moment=times[index], days=RESOLUTION_WINDOW_DAYS
            )
            for candidate in ordered[start:index]:
                if str(candidate.type) != MemoryType.PROBLEM.value or candidate.is_resolved:
                    continue
                if _shared_entities(memory, candidate) or _word_overlap(memory, candidate) >= 0.3:
                    proposal = LinkProposal(
                        source_memory_id=memory.id,
                        target_memory_id=candidate.id,
                        link_type=LinkType.RESOLVED_BY,
                        confidence=0.7,
                        rationale="Later statement reports the earlier problem as resolved.",
                    )
                    proposals[proposal.key] = proposal

        # 3. Memories about the same entity, close together in time, are related.
        for candidate in ordered[max(0, index - RELATED_LOOKBACK) : index]:
            if candidate.id == memory.id:
                continue
            gap = days_between(candidate.last_seen_at, memory.last_seen_at)
            if gap > RELATED_WINDOW_DAYS:
                continue
            shared = _shared_entities(memory, candidate)
            overlap = _word_overlap(memory, candidate)
            if not shared and overlap < 0.35:
                continue
            proposal = LinkProposal(
                source_memory_id=memory.id,
                target_memory_id=candidate.id,
                link_type=LinkType.RELATES_TO,
                confidence=round(min(0.8, 0.4 + 0.3 * overlap + (0.15 if shared else 0)), 3),
                rationale=(
                    f"Recorded within {int(gap)} day(s)"
                    + (f" about {', '.join(sorted(shared))}" if shared else " with similar wording")
                    + "."
                ),
            )
            proposals.setdefault(proposal.key, proposal)

    return sorted(proposals.values(), key=lambda item: -item.confidence)


def chain_for(
    memory_id: str, links: Sequence[LinkProposal], *, depth: int = 2
) -> list[LinkProposal]:
    """Follow ``caused_by`` edges back from an outcome to build a causal chain."""
    chain: list[LinkProposal] = []
    frontier = {memory_id}
    seen: set[tuple[str, str, str]] = set()
    for _ in range(max(1, depth)):
        next_frontier: set[str] = set()
        for link in links:
            if link.source_memory_id in frontier and link.key not in seen:
                seen.add(link.key)
                chain.append(link)
                next_frontier.add(link.target_memory_id)
        frontier = next_frontier
        if not frontier:
            break
    return chain
