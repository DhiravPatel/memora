"""Question understanding.

Before retrieval runs, the question is parsed into what it is actually asking for: an
intent, the memory types involved, the entities named, and a time window. This is cheap,
explainable and, unlike a model, it behaves the same way every time — which matters
because it decides what an agent is allowed to see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from common.enums import MemoryType
from common.time import days_ago, utcnow
from nlp.entities import from_gazetteer, from_proper_nouns
from nlp.lexicon import SYNONYMS
from nlp.tokenize import content_words, correct_spelling, expand_contractions, lemmas, tokenize


class QuestionIntent(StrEnum):
    WHY = "why"                 # causal: why did X happen
    PROBLEMS = "problems"       # what is wrong
    PREFERENCES = "preferences" # how they want to be treated
    GOALS = "goals"             # what they are trying to do
    HISTORY = "history"         # what happened, in order
    WHEN = "when"               # timing of something
    WHO = "who"                 # people and companies
    HOW_MANY = "how_many"       # counting
    RISK = "risk"               # churn / escalation risk
    STATUS = "status"           # current state of a thing
    SUMMARY = "summary"         # overall picture
    GENERIC = "generic"


_INTENT_PATTERNS: tuple[tuple[QuestionIntent, tuple[str, ...]], ...] = (
    (QuestionIntent.HOW_MANY, ("how many", "how often", "number of", "count of", "how much")),
    (QuestionIntent.WHEN, ("when did", "when was", "what date", "how long ago", "since when")),
    (QuestionIntent.WHO, ("who is", "who are", "who at", "who from", "which person", "who works")),
    (QuestionIntent.WHY, ("why did", "why is", "why has", "why are", "what caused", "reason for",
                          "reason why", "what led to", "root cause")),
    (QuestionIntent.RISK, ("at risk", "churn risk", "going to cancel", "will they cancel",
                           "likely to churn", "risk of", "are they happy", "escalation")),
    (QuestionIntent.PROBLEMS, ("what problems", "what issues", "any problems", "any issues",
                               "what is broken", "what's broken", "what went wrong", "complaints",
                               "errors", "bugs")),
    (QuestionIntent.PREFERENCES, ("preference", "prefers", "how should we contact", "contact them",
                                  "how do they want", "what do they like", "dislike")),
    (QuestionIntent.GOALS, ("what are they trying", "their goal", "goals", "objective",
                            "what do they want to achieve", "trying to do")),
    (QuestionIntent.STATUS, ("current status", "what plan", "which plan", "are they on",
                             "status of", "is it resolved", "still broken", "still failing",
                             "resolved", "fixed", "sorted out", "is it working")),
    (QuestionIntent.HISTORY, ("what happened", "history", "timeline", "walk me through",
                              "recent activity", "what have they done")),
    (QuestionIntent.SUMMARY, ("summarise", "summarize", "summary", "tell me about",
                              "who is this customer", "overview", "brief me", "context")),
)

_TYPE_HINTS: dict[MemoryType, tuple[str, ...]] = {
    MemoryType.PROBLEM: ("problem", "issue", "error", "bug", "broken", "fail", "complaint",
                         "wrong", "outage", "crash", "stuck"),
    MemoryType.PREFERENCE: ("prefer", "preference", "contact", "channel", "like", "dislike",
                            "opt out", "unsubscribe"),
    MemoryType.GOAL: ("goal", "objective", "trying", "achieve", "target", "plan to", "roadmap"),
    MemoryType.SUBSCRIPTION: ("plan", "subscription", "billing", "invoice", "upgrade", "downgrade",
                              "price", "payment", "refund", "seat", "renewal"),
    MemoryType.FEEDBACK: ("feedback", "review", "nps", "rating", "satisfaction", "say about",
                          "think about", "happy", "unhappy"),
    MemoryType.INTENT: ("intent", "considering", "evaluating", "churn", "cancel", "switch",
                        "competitor", "renew"),
    MemoryType.BEHAVIOR: ("usage", "using", "use", "activity", "feature", "behaviour", "behavior",
                          "adoption", "login"),
    MemoryType.RELATIONSHIP: ("team", "colleague", "company", "who", "manager", "owner", "works"),
}

_RELATIVE_WINDOWS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\btoday\b"), 1),
    (re.compile(r"\byesterday\b"), 2),
    (re.compile(r"\bthis week\b|\blast (?:7|seven) days\b"), 7),
    (re.compile(r"\blast week\b"), 14),
    (re.compile(r"\brecent(?:ly)?\b|\blately\b|\bnow\b"), 30),
    (re.compile(r"\bthis month\b|\blast 30 days\b"), 30),
    (re.compile(r"\blast month\b"), 60),
    (re.compile(r"\blast quarter\b|\blast (?:3|three) months\b"), 90),
    (re.compile(r"\bthis year\b|\blast year\b"), 365),
    (re.compile(r"\bever\b|\ball time\b|\bhistorically\b"), 0),
)

_EXPLICIT_WINDOW = re.compile(r"\blast (\d{1,3}) (day|days|week|weeks|month|months|year|years)\b")
_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30,
              "year": 365, "years": 365}

# Events a "why" question usually asks about.
_CAUSAL_TARGETS: dict[str, tuple[str, ...]] = {
    "downgrade": ("downgrade", "downgraded", "downgrading"),
    "cancel": ("cancel", "cancelled", "canceled", "cancellation", "churn", "churned", "left"),
    "upgrade": ("upgrade", "upgraded"),
    "complaint": ("complain", "complained", "complaint", "angry", "upset", "escalate"),
    "contact_support": ("contact support", "contacted support", "raised a ticket", "open a ticket"),
    "refund": ("refund", "refunded", "chargeback"),
}


@dataclass(slots=True)
class QuestionAnalysis:
    query: str
    intent: QuestionIntent = QuestionIntent.GENERIC
    types: list[MemoryType] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    expanded_keywords: list[str] = field(default_factory=list)
    causal_target: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    wants_recent: bool = False
    is_aggregate: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent.value,
            "types": [memory_type.value for memory_type in self.types],
            "entity_names": self.entity_names,
            "keywords": self.keywords,
            "expanded_keywords": self.expanded_keywords,
            "causal_target": self.causal_target,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "wants_recent": self.wants_recent,
            "is_aggregate": self.is_aggregate,
        }

    @property
    def search_text(self) -> str:
        """Query text used for retrieval, widened with synonyms."""
        return " ".join([self.query, *self.expanded_keywords])


def expand(
    keywords: list[str], *, learned: dict[str, tuple[str, ...]] | None = None
) -> list[str]:
    """Add synonyms so "billing" also finds "invoice" and "charge".

    ``learned`` is the project's own mined vocabulary, which is what lets a question about
    "the loader" find memories that say "importer". Shipped synonyms are applied first so
    a project cannot lose the general-purpose ones by learning something odd.
    """
    expanded: list[str] = []
    for word in keywords:
        candidates = (*SYNONYMS.get(word, ()), *(learned or {}).get(word, ()))
        for synonym in candidates:  # exact lemma match only: no fuzzy drift
            if synonym not in expanded and synonym not in keywords:
                expanded.append(synonym)
    return expanded


def detect_intent(lowered: str) -> QuestionIntent:
    for intent, patterns in _INTENT_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return intent
    if lowered.startswith("why"):
        return QuestionIntent.WHY
    if lowered.startswith("when"):
        return QuestionIntent.WHEN
    if lowered.startswith("who"):
        return QuestionIntent.WHO
    if lowered.startswith(("what happened", "what has happened")):
        return QuestionIntent.HISTORY
    return QuestionIntent.GENERIC


def detect_causal_target(lowered: str) -> str | None:
    # Match on tokens so punctuation ("downgrade?") does not hide the target.
    padded = f" {' '.join(tokenize(lowered))} "
    for target, markers in _CAUSAL_TARGETS.items():
        if any(f" {marker} " in padded for marker in markers):
            return target
    return None


def analyze(
    query: str, *, learned_synonyms: dict[str, tuple[str, ...]] | None = None
) -> QuestionAnalysis:
    cleaned = correct_spelling(query.strip())
    lowered = expand_contractions(cleaned).lower()
    lemma_text = " ".join(lemmas(cleaned))

    analysis = QuestionAnalysis(query=query.strip())
    analysis.intent = detect_intent(lowered)
    analysis.causal_target = detect_causal_target(lowered)
    analysis.keywords = [word for word in content_words(cleaned) if len(word) > 2][:12]
    analysis.expanded_keywords = expand(analysis.keywords, learned=learned_synonyms)
    analysis.is_aggregate = analysis.intent is QuestionIntent.HOW_MANY

    for memory_type, hints in _TYPE_HINTS.items():
        if any(hint in lowered or hint in lemma_text for hint in hints):
            analysis.types.append(memory_type)

    # Intent implies types even when the wording does not.
    implied = {
        QuestionIntent.PROBLEMS: [MemoryType.PROBLEM],
        QuestionIntent.PREFERENCES: [MemoryType.PREFERENCE],
        QuestionIntent.GOALS: [MemoryType.GOAL, MemoryType.INTENT],
        QuestionIntent.RISK: [MemoryType.PROBLEM, MemoryType.INTENT, MemoryType.SUBSCRIPTION],
        QuestionIntent.WHY: [MemoryType.PROBLEM, MemoryType.SUBSCRIPTION, MemoryType.FEEDBACK],
        QuestionIntent.WHO: [MemoryType.RELATIONSHIP],
    }.get(analysis.intent, [])
    for memory_type in implied:
        if memory_type not in analysis.types:
            analysis.types.append(memory_type)

    explicit = _EXPLICIT_WINDOW.search(lowered)
    if explicit:
        analysis.since = days_ago(int(explicit.group(1)) * _UNIT_DAYS[explicit.group(2)])
        analysis.wants_recent = True
    else:
        for pattern, days in _RELATIVE_WINDOWS:
            if pattern.search(lowered):
                if days:
                    analysis.since = days_ago(days)
                    analysis.wants_recent = True
                break
    if analysis.since is not None:
        analysis.until = utcnow()

    tokens = set(tokenize(cleaned))
    for hit in from_gazetteer(cleaned):
        if hit.name not in analysis.entity_names:
            analysis.entity_names.append(hit.name)
    for hit in from_proper_nouns(cleaned):
        if hit.name.lower() in tokens and hit.name not in analysis.entity_names:
            analysis.entity_names.append(hit.name)

    return analysis
