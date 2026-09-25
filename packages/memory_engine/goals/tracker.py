"""Goal tracking: from "we want to…" to "they did".

Extraction already recognises a goal when a customer states one. What it cannot do is
notice, three weeks later, that the goal happened — because the message that closes a goal
rarely mentions it ("SSO is live for everyone now"). This module does that matching: it
reduces a goal to the lemmas it is *about*, then scores every later memory against those
lemmas, and moves the goal only when the overlap is strong enough and the language says
something happened.

Pure functions over views. The caller decides what to persist, which keeps the transition
rules trivially testable and keeps one place — ``decide`` — that anyone can read to find
out exactly when a goal closes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.enums import GoalStatus, MemoryType
from common.time import days_between, ensure_utc, utcnow
from nlp.answer import MemoryView
from nlp.lexicon import NEGATION_SCOPE, NEGATIONS
from nlp.tokenize import content_words, lemmatize

# How much of the goal's vocabulary a later memory must share before it counts as being
# about that goal. Tuned against the lexical embedder's behaviour: below ~0.3 unrelated
# product chatter starts matching, above ~0.6 genuine completions are missed.
MATCH_THRESHOLD = 0.34
# A single strong completion phrase can carry a slightly weaker lexical match.
STRONG_MATCH_THRESHOLD = 0.28

STALE_AFTER_DAYS = 30
MIN_KEYWORDS = 2
MAX_KEYWORDS = 12

# Words that appear in goal statements but say nothing about the goal itself.
GOAL_FRAME_WORDS = frozenset(
    lemmatize(word)
    for word in (
        "want", "like", "plan", "planning", "hope", "need", "try", "trying", "look",
        "looking", "goal", "aim", "intend", "would", "could", "should", "will", "going",
        "able", "get", "make", "take", "use", "team", "month", "quarter", "year", "week",
        # Every stored memory is about "the customer" — the third-person rewrite puts it
        # there — so it can never tell one goal's evidence from another's.
        "customer", "customers", "client",
    )
)

ACHIEVEMENT_CUES: tuple[str, ...] = (
    "now live", "is live", "went live", "launched", "rolled out", "rollout complete",
    "completed", "complete", "finished", "done", "shipped", "migrated", "switched over",
    "up and running", "all set", "we did it", "set up", "enabled", "in production",
    "working now", "achieved", "hit our", "reached our", "successfully",
)
PROGRESS_CUES: tuple[str, ...] = (
    "started", "starting", "began", "working on", "in progress", "halfway", "almost",
    "nearly", "getting there", "next step", "first step", "testing", "trialling",
    "trialing", "piloting", "rolling out", "setting up", "configuring", "migrating",
)
ABANDONMENT_CUES: tuple[str, ...] = (
    "gave up", "giving up", "no longer", "not going ahead", "cancelled", "canceled",
    "shelved", "paused indefinitely", "dropped", "decided against", "not doing",
    "changed our mind", "abandoned", "postponed indefinitely", "went with",
)

def _lemma_forms(cues: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((phrase, " ".join(lemmatize(word) for word in phrase.split())) for phrase in cues)


_LEMMA_ACHIEVEMENT = _lemma_forms(ACHIEVEMENT_CUES)
_LEMMA_ABANDON = _lemma_forms(ABANDONMENT_CUES)


@dataclass(slots=True, frozen=True)
class GoalView:
    """A tracked goal, as the rules see it."""

    id: str
    statement: str
    keywords: tuple[str, ...]
    status: GoalStatus
    progress: float
    opened_at: datetime
    last_signal_at: datetime
    memory_id: str | None = None


@dataclass(slots=True, frozen=True)
class Transition:
    """A decision about one goal: what changes, why, and what proves it."""

    goal_id: str
    status: GoalStatus
    progress: float
    confidence: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def closes(self) -> bool:
        return self.status.is_closed


@dataclass(slots=True, frozen=True)
class GoalCandidate:
    """A goal memory that is not yet tracked."""

    memory_id: str
    statement: str
    keywords: tuple[str, ...]
    confidence: float
    stated_at: datetime


def keywords_for(text: str) -> tuple[str, ...]:
    """The lemmas a goal is about, with the "I want to" framing stripped out."""
    seen: dict[str, None] = {}
    for word in content_words(text):
        lemma = lemmatize(word)
        if len(lemma) < 3 or lemma in GOAL_FRAME_WORDS:
            continue
        seen.setdefault(lemma, None)
    return tuple(seen)[:MAX_KEYWORDS]


def overlap(keywords: Sequence[str], text: str) -> float:
    """Share of the goal's vocabulary present in ``text`` (0-1)."""
    if not keywords:
        return 0.0
    lemmas = {lemmatize(word) for word in content_words(text)}
    if not lemmas:
        return 0.0
    hits = sum(1 for keyword in keywords if keyword in lemmas)
    return round(hits / len(keywords), 3)


def _negated_before(text: str, index: int) -> bool:
    """Whether a negation sits close enough in front of a cue to reverse it.

    "SSO is not live yet" must not close a goal that "SSO is live" would.
    """
    preceding = text[:index].split()[-NEGATION_SCOPE:]
    return any(word.strip(",.;:") in NEGATIONS for word in preceding)


_WORD = re.compile(r"[a-z0-9']+")


def _words_of(text: str) -> str:
    """Lowercase words joined by single spaces — punctuation gone, so a cue can be matched
    as whole words."""
    return " ".join(_WORD.findall(text.lower()))


def _find(words: str, phrase: str) -> int:
    """Where ``phrase`` starts in ``words`` as whole words, or -1: "done" is not in
    "downgraded", however the two lemmatise."""
    index = f" {words} ".find(f" {phrase} ")
    return index if index != -1 else -1


def _cue_hit(
    text: str, cues: Sequence[tuple[str, str]], *, keywords: Sequence[str] = ()
) -> str | None:
    """Find a cue in the raw text, then — more loosely — in its lemmas; whole words only.

    A cue whose lemma is one of the goal's own keywords only counts as written: for "launch
    automation", "we launched it" is the goal done, but "the launch is delayed" is only the
    goal mentioned.
    """
    lowered = _words_of(text)
    for phrase, _ in cues:
        index = _find(lowered, phrase)
        if index != -1 and not _negated_before(lowered, index):
            return phrase

    own = set(keywords)
    lemma_text = " ".join(lemmatize(word) for word in lowered.split())
    for phrase, lemma_phrase in cues:
        if lemma_phrase in own:
            continue
        index = _find(lemma_text, lemma_phrase)
        if index != -1 and not _negated_before(lemma_text, index):
            return phrase
    return None


def _progress_cue(text: str) -> str | None:
    """Progress is matched on the raw text only, because tense *is* the signal.

    Lemmatising collapses "rolling out" into "rolled out", which would let a note about
    work in progress close the goal it is progressing. Matching the surface form keeps the
    two apart, at the cost of reading "we finished rolling it out" as progress — the
    conservative direction to be wrong in.
    """
    lowered = _words_of(text)
    for phrase in PROGRESS_CUES:
        index = _find(lowered, phrase)
        if index != -1 and not _negated_before(lowered, index):
            return phrase
    return None


def candidates(memories: Sequence[MemoryView], *, tracked_memory_ids: Sequence[str] = ()) -> list[GoalCandidate]:
    """Goal memories that deserve a tracked goal of their own."""
    already = set(tracked_memory_ids)
    found: list[GoalCandidate] = []
    for memory in memories:
        if memory.id in already or str(memory.type) != MemoryType.GOAL.value:
            continue
        keywords = keywords_for(memory.content)
        if len(keywords) < MIN_KEYWORDS:
            # "We want to grow" has nothing to match later evidence against.
            continue
        found.append(
            GoalCandidate(
                memory_id=memory.id,
                statement=memory.content,
                keywords=keywords,
                confidence=memory.confidence,
                stated_at=ensure_utc(memory.first_seen_at),
            )
        )
    return found


def duplicate_of(candidate: GoalCandidate, goals: Sequence[GoalView]) -> GoalView | None:
    """Whether an existing goal already covers this statement."""
    for goal in goals:
        if goal.memory_id and goal.memory_id == candidate.memory_id:
            return goal
        if not goal.keywords or not candidate.keywords:
            continue
        shared = len(set(goal.keywords) & set(candidate.keywords))
        ratio = shared / min(len(goal.keywords), len(candidate.keywords))
        if ratio >= 0.7:
            return goal
    return None


def decide(
    goal: GoalView,
    *,
    memories: Sequence[MemoryView],
    now: datetime | None = None,
) -> Transition | None:
    """Work out whether later memories have moved this goal.

    Only memories recorded *after* the goal was stated are considered: a problem report
    from last month cannot be evidence that this week's goal was abandoned.
    """
    now = now or utcnow()
    if goal.status.is_closed:
        return None

    later = [
        memory
        for memory in memories
        if ensure_utc(memory.last_seen_at) >= goal.opened_at and memory.id != goal.memory_id
    ]

    best: tuple[float, MemoryView, str, str] | None = None  # (score, memory, kind, cue)
    for memory in later:
        score = overlap(goal.keywords, memory.content)
        if score < STRONG_MATCH_THRESHOLD:
            continue

        abandon_cue = _cue_hit(memory.content, _LEMMA_ABANDON, keywords=goal.keywords)
        # Progress is checked before completion: "setting up SSO" is not "SSO is set up".
        progress_cue = _progress_cue(memory.content)
        achieve_cue = None if progress_cue else _cue_hit(memory.content, _LEMMA_ACHIEVEMENT, keywords=goal.keywords)

        if abandon_cue:
            kind, cue = "abandoned", abandon_cue
        elif achieve_cue:
            kind, cue = "achieved", achieve_cue
        elif progress_cue or score >= MATCH_THRESHOLD:
            kind, cue = "progress", progress_cue or "related activity"
        else:
            continue

        # A weak lexical match needs an explicit cue to count for anything.
        if score < MATCH_THRESHOLD and kind == "progress":
            continue

        weight = score + (0.3 if kind in ("achieved", "abandoned") else 0.0)
        if best is None or weight > best[0]:
            best = (weight, memory, kind, cue)

    if best is not None:
        _, memory, kind, cue = best
        score = overlap(goal.keywords, memory.content)
        evidence = {
            "kind": kind,
            "memory_id": memory.id,
            "match": score,
            "cue": cue,
            "at": ensure_utc(memory.last_seen_at).isoformat(),
        }
        if kind == "achieved":
            return Transition(
                goal_id=goal.id,
                status=GoalStatus.ACHIEVED,
                progress=1.0,
                confidence=round(min(0.95, 0.55 + score / 2), 3),
                reason=f"“{cue}” recorded against this goal",
                evidence=evidence,
            )
        if kind == "abandoned":
            return Transition(
                goal_id=goal.id,
                status=GoalStatus.ABANDONED,
                progress=goal.progress,
                confidence=round(min(0.9, 0.5 + score / 2), 3),
                reason=f"“{cue}” recorded against this goal",
                evidence=evidence,
            )

        # Progress: each supporting memory advances the goal, with diminishing returns so
        # chatter alone can never reach 100%.
        progress = round(min(0.9, max(goal.progress, 0.2) + 0.2 * score), 3)
        if progress <= goal.progress and goal.status == GoalStatus.PROGRESSING:
            return None
        return Transition(
            goal_id=goal.id,
            status=GoalStatus.PROGRESSING,
            progress=progress,
            confidence=round(min(0.85, 0.4 + score / 2), 3),
            reason="related activity recorded",
            evidence=evidence,
        )

    # Nothing moved it. Ageing out is a state, not a closure: the goal may still be alive,
    # and saying so is more useful than pretending it was abandoned.
    idle_days = days_between(goal.last_signal_at, now)
    if goal.status in (GoalStatus.OPEN, GoalStatus.PROGRESSING) and idle_days >= STALE_AFTER_DAYS:
        return Transition(
            goal_id=goal.id,
            status=GoalStatus.STALLED,
            progress=goal.progress,
            confidence=0.5,
            reason=f"no supporting evidence for {int(idle_days)} days",
            evidence={"kind": "stalled", "idle_days": int(idle_days), "at": now.isoformat()},
        )
    return None


def summarise(goals: Sequence[GoalView]) -> str:
    """One line for a customer header."""
    if not goals:
        return "No goals recorded."
    live = [goal for goal in goals if not goal.status.is_closed]
    achieved = [goal for goal in goals if goal.status == GoalStatus.ACHIEVED]
    parts = []
    if live:
        parts.append(f"{len(live)} in flight")
    if achieved:
        parts.append(f"{len(achieved)} reached")
    stalled = [goal for goal in goals if goal.status == GoalStatus.STALLED]
    if stalled:
        parts.append(f"{len(stalled)} stalled")
    return ", ".join(parts) or "No goals recorded."
