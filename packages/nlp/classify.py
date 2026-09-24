"""Statement classification: which kind of memory is this sentence?

Scores every memory type from cue phrases, negation, sentiment and the event type that
carried the sentence, then returns the winner with the exact cues that produced it. The
explanation is part of the contract: an operator who disagrees with a classification can
see which phrase caused it and fix the lexicon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from common.enums import MemoryType
from nlp.lexicon import CUES, NEGATIONS, RESOLUTION_CUES
from nlp.sentiment import Sentiment, analyze
from nlp.tokenize import expand_contractions, lemmatized_text, tokenize

# Event types bias the classification of their own text.
EVENT_TYPE_PRIORS: dict[str, tuple[MemoryType, float]] = {
    "support_message": (MemoryType.PROBLEM, 0.35),
    "support_ticket_created": (MemoryType.PROBLEM, 0.45),
    "integration_failed": (MemoryType.PROBLEM, 0.8),
    "payment_failed": (MemoryType.PROBLEM, 0.8),
    "error_reported": (MemoryType.PROBLEM, 0.7),
    "feedback": (MemoryType.FEEDBACK, 0.7),
    "feedback_submitted": (MemoryType.FEEDBACK, 0.7),
    "nps_submitted": (MemoryType.FEEDBACK, 0.8),
    "review_submitted": (MemoryType.FEEDBACK, 0.8),
    "subscription_changed": (MemoryType.SUBSCRIPTION, 0.9),
    "subscription_upgraded": (MemoryType.SUBSCRIPTION, 0.9),
    "subscription_downgraded": (MemoryType.SUBSCRIPTION, 0.9),
    "subscription_cancelled": (MemoryType.SUBSCRIPTION, 0.9),
    "cancellation_requested": (MemoryType.INTENT, 0.8),
    "invoice_paid": (MemoryType.SUBSCRIPTION, 0.6),
    "goal_created": (MemoryType.GOAL, 0.9),
    "feature_used": (MemoryType.BEHAVIOR, 0.7),
    "integration_connected": (MemoryType.BEHAVIOR, 0.6),
    "onboarding_completed": (MemoryType.BEHAVIOR, 0.6),
    "preferences_updated": (MemoryType.PREFERENCE, 0.8),
    "profile_updated": (MemoryType.FACT, 0.5),
    "purchase": (MemoryType.BEHAVIOR, 0.6),
    "order_placed": (MemoryType.BEHAVIOR, 0.6),
    "demo_requested": (MemoryType.INTENT, 0.7),
    "trial_started": (MemoryType.INTENT, 0.6),
}

# Lemmatised cue index, built once at import.
_LEMMA_CUES: dict[MemoryType, tuple[tuple[str, str, float], ...]] = {
    memory_type: tuple((phrase, lemmatized_text(phrase), weight) for phrase, weight in cues)
    for memory_type, cues in CUES.items()
}
_LEMMA_RESOLUTION = tuple((phrase, lemmatized_text(phrase)) for phrase in RESOLUTION_CUES)


@dataclass(slots=True)
class Classification:
    type: MemoryType
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)
    cues: list[str] = field(default_factory=list)
    negated_cues: list[str] = field(default_factory=list)
    resolved: bool = False
    sentiment: Sentiment | None = None
    margin: float = 0.0

    def explain(self) -> dict[str, object]:
        return {
            "type": self.type.value,
            "confidence": round(self.confidence, 3),
            "margin": round(self.margin, 3),
            "cues": self.cues,
            "negated_cues": self.negated_cues,
            "resolved": self.resolved,
            "scores": {key: round(value, 3) for key, value in sorted(
                self.scores.items(), key=lambda item: -item[1]
            )},
            "sentiment": self.sentiment.as_dict() if self.sentiment else None,
        }


# Where one statement ends and the next begins, for negation. "QuickBooks will not
# connect, it keeps rejecting the credentials" is two complaints; the "not" of the first
# must not read as denying the second.
_CLAUSE_BREAK = re.compile(r"[,;:!?.]+|\s(?:but|however|although|though|whereas)\s", re.IGNORECASE)

# An event type's prior below this only reinforces evidence the sentence already carries
# for that type. A message in a support thread is not a problem report because of where it
# was sent: "the campaign went out this morning" is news, not a complaint.
WEAK_PRIOR = 0.5


# A fix that has not happened yet is not a resolution: "until SSO is fixed", "once it is
# resolved", "please get this sorted", "it needs to be fixed". Lemmatised forms.
_UNRESOLVED_MARKERS = frozenset(
    lemmatized_text(word)
    for word in ("until", "unless", "once", "if", "when", "need", "should", "please", "must", "hope", "wait", "expect", "able")
)


def _resolution_in(clauses: list[str]) -> bool:
    """Whether a clause reports something fixed, with no marker before the cue saying the
    fix is still to come."""
    for clause in clauses:
        for _, cue in _LEMMA_RESOLUTION:
            position = clause.find(f" {cue} ")
            if position < 0:
                continue
            if set(clause[:position].split()) & _UNRESOLVED_MARKERS:
                continue
            return True
    return False


def _clauses(sentence: str) -> list[str]:
    parts = [part for part in _CLAUSE_BREAK.split(sentence) if part and part.strip()]
    return [f" {lemmatized_text(part)} " for part in parts]


def _cue_is_negated(clauses: list[str], cue: str) -> bool:
    """True when a negation sits shortly before the cue *in the same clause* ("no problem",
    "not an issue")."""
    for clause in clauses:
        position = clause.find(f" {cue} ")
        if position < 0:
            continue
        window = clause[max(0, position - 24) : position + 1]
        return any(f" {negation} " in f" {window} " for negation in NEGATIONS)
    return False


def classify(sentence: str, *, event_type: str = "", prior_weight: float = 1.0) -> Classification:
    lemma_sentence = f" {lemmatized_text(sentence)} "
    clauses = _clauses(sentence)
    sentiment = analyze(sentence)
    scores: dict[MemoryType, float] = {}
    matched: list[str] = []
    negated: list[str] = []
    problem_denied = False

    for memory_type, cues in _LEMMA_CUES.items():
        best = 0.0
        hits = 0
        for phrase, lemma_cue, weight in cues:
            # Word-boundary match only: a substring match makes "rating" fire on "migrate".
            if f" {lemma_cue} " not in lemma_sentence:
                continue
            if _cue_is_negated(clauses, lemma_cue):
                negated.append(phrase)
                # "no problem" is the opposite of a problem report.
                if memory_type is MemoryType.PROBLEM:
                    best = max(0.0, best - weight * 0.5)
                    problem_denied = True
                continue
            hits += 1
            best = max(best, weight)
            matched.append(phrase)
        if best > 0:
            # Extra independent cues add confidence with diminishing returns.
            scores[memory_type] = min(1.4, best + 0.12 * max(0, hits - 1))

    # Sentiment nudges: strong feeling means the statement is a problem or feedback,
    # never a neutral behavioural note.
    if sentiment.negativity >= 0.35:
        scores[MemoryType.PROBLEM] = scores.get(MemoryType.PROBLEM, 0.0) + sentiment.negativity * 0.5
        scores[MemoryType.FEEDBACK] = scores.get(MemoryType.FEEDBACK, 0.0) + sentiment.negativity * 0.25
    if sentiment.positivity >= 0.35:
        scores[MemoryType.FEEDBACK] = scores.get(MemoryType.FEEDBACK, 0.0) + sentiment.positivity * 0.5
    if sentiment.churn_risk >= 0.5:
        scores[MemoryType.INTENT] = scores.get(MemoryType.INTENT, 0.0) + sentiment.churn_risk * 0.6

    prior = EVENT_TYPE_PRIORS.get(event_type.strip().lower())
    if prior is not None:
        prior_type, weight = prior
        if weight * prior_weight >= WEAK_PRIOR or scores.get(prior_type, 0.0) > 0:
            scores[prior_type] = scores.get(prior_type, 0.0) + weight * prior_weight

    resolved = _resolution_in(clauses) and not sentiment.is_negative
    if resolved or problem_denied:
        # An explicit denial or a "it works now" outweighs the event type's prior.
        scores[MemoryType.PROBLEM] = scores.get(MemoryType.PROBLEM, 0.0) * 0.3
        scores[MemoryType.FACT] = scores.get(MemoryType.FACT, 0.0) + 0.5
        scores = {key: value for key, value in scores.items() if value > 0.05}

    if not scores:
        return Classification(
            type=MemoryType.FACT,
            confidence=0.45,
            scores={},
            cues=[],
            negated_cues=negated,
            resolved=resolved,
            sentiment=sentiment,
        )

    ranked = sorted(scores.items(), key=lambda item: -item[1])
    winner, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top - runner_up

    # Confidence blends cue strength with how clearly the winner beat the alternatives.
    confidence = min(0.99, 0.45 + 0.35 * min(1.0, top) + 0.2 * min(1.0, margin))
    if len(tokenize(expand_contractions(sentence))) < 4:
        confidence *= 0.85

    return Classification(
        type=winner,
        confidence=round(confidence, 4),
        scores={key.value: value for key, value in scores.items()},
        cues=sorted(set(matched)),
        negated_cues=sorted(set(negated)),
        resolved=resolved,
        sentiment=sentiment,
        margin=margin,
    )
