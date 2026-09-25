"""Lexicon-based sentiment, urgency and churn-risk scoring.

Used to decide how much a statement should matter: an angry, urgent message about a
blocked production integration is worth more than a neutral note, and a sentence that
mentions cancelling is worth more still.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nlp.lexicon import (
    CHURN_WORDS,
    DIMINISHERS,
    INTENSIFIERS,
    NEGATION_SCOPE,
    NEGATION_STOPWORDS,
    NEGATIONS,
    NEGATIVE_WORDS,
    POSITIVE_WORDS,
    URGENCY_WORDS,
)
from nlp.tokenize import expand_contractions, tokenize


@dataclass(slots=True, frozen=True)
class Sentiment:
    polarity: float  # -1 (very negative) … +1 (very positive)
    negativity: float  # 0 … 1
    positivity: float  # 0 … 1
    urgency: float  # 0 … 1
    churn_risk: float  # 0 … 1
    exclamations: int = 0
    shouted: bool = False

    @property
    def is_negative(self) -> bool:
        return self.polarity <= -0.2

    @property
    def is_positive(self) -> bool:
        return self.polarity >= 0.2

    def as_dict(self) -> dict[str, float | int | bool]:
        return {
            "polarity": round(self.polarity, 3),
            "negativity": round(self.negativity, 3),
            "positivity": round(self.positivity, 3),
            "urgency": round(self.urgency, 3),
            "churn_risk": round(self.churn_risk, 3),
            "exclamations": self.exclamations,
            "shouted": self.shouted,
        }


def negated_positions(tokens: list[str]) -> set[int]:
    """Indices of tokens that fall inside a negation's scope."""
    negated: set[int] = set()
    for index, token in enumerate(tokens):
        if token not in NEGATIONS:
            continue
        for offset in range(1, NEGATION_SCOPE + 1):
            position = index + offset
            if position >= len(tokens) or tokens[position] in NEGATION_STOPWORDS:
                break
            negated.add(position)
    return negated


def analyze(text: str) -> Sentiment:
    expanded = expand_contractions(text)
    tokens = tokenize(expanded)
    if not tokens:
        return Sentiment(polarity=0.0, negativity=0.0, positivity=0.0, urgency=0.0, churn_risk=0.0)

    negated = negated_positions(tokens)
    positive = negative = urgency = churn = 0.0

    for index, token in enumerate(tokens):
        modifier = 1.0
        if index > 0:
            previous = tokens[index - 1]
            modifier = INTENSIFIERS.get(previous, DIMINISHERS.get(previous, 1.0))

        flipped = index in negated
        if token in POSITIVE_WORDS:
            score = POSITIVE_WORDS[token] * modifier
            # "not great" is a complaint, not praise.
            negative += score if flipped else 0.0
            positive += 0.0 if flipped else score
        if token in NEGATIVE_WORDS:
            score = NEGATIVE_WORDS[token] * modifier
            positive += score * 0.6 if flipped else 0.0
            negative += 0.0 if flipped else score
        if token in URGENCY_WORDS:
            urgency += URGENCY_WORDS[token] * modifier
        if token in CHURN_WORDS and not flipped:
            churn += CHURN_WORDS[token]

    exclamations = text.count("!")
    words = [word for word in text.split() if len(word) > 2]
    shouted = bool(words) and sum(word.isupper() for word in words) / len(words) > 0.6
    if exclamations:
        negative *= 1.0 + min(0.3, 0.1 * exclamations)
        urgency += min(0.3, 0.1 * exclamations)
    if shouted:
        negative *= 1.2
        urgency += 0.2

    scale = max(1.0, len(tokens) / 12)
    positivity = min(1.0, positive / scale)
    negativity = min(1.0, negative / scale)
    return Sentiment(
        polarity=round(max(-1.0, min(1.0, positivity - negativity)), 4),
        negativity=round(negativity, 4),
        positivity=round(positivity, 4),
        urgency=round(min(1.0, urgency), 4),
        churn_risk=round(min(1.0, churn), 4),
        exclamations=exclamations,
        shouted=shouted,
    )


# ------------------------------------------------------------------ feedback

# A score's band, as the NPS/CSAT template writes it: "a satisfaction score of 4 (detractor)".
_BAND = re.compile(r"\((detractor|passive|promoter)\)", re.IGNORECASE)
_TONE_OF_BAND = {"detractor": "negative", "passive": "neutral", "promoter": "positive"}
# The fact document's line between mild and neutral (§26 1.1).
TONE_THRESHOLD = 0.1


def feedback_band(content: str, attributes: Mapping[str, Any] | None = None) -> str | None:
    """detractor, passive or promoter, when the feedback is a score — from the band the
    template recorded, or the words it wrote for memories made before the band was kept."""
    band = (attributes or {}).get("band")
    if isinstance(band, str) and band.lower() in _TONE_OF_BAND:
        return band.lower()
    match = _BAND.search(content or "")
    return match.group(1).lower() if match else None


def feedback_polarity(content: str, attributes: Mapping[str, Any] | None = None) -> float:
    """The sentiment recorded when the feedback was extracted — or, for a memory that has
    none, the sentiment of its own words."""
    sentiment = (attributes or {}).get("sentiment")
    if isinstance(sentiment, Mapping) and sentiment.get("polarity") is not None:
        try:
            return float(sentiment["polarity"])
        except (TypeError, ValueError):
            pass
    return analyze(content or "").polarity


def feedback_tone(content: str, attributes: Mapping[str, Any] | None = None) -> str:
    """negative, positive or neutral — one answer for health, the fact document, "what
    changed" and the journey. A score says it through its band (a 4 is a detractor
    whatever the sentence around it); anything else through its sentiment."""
    band = feedback_band(content, attributes)
    if band is not None:
        return _TONE_OF_BAND[band]
    polarity = feedback_polarity(content, attributes)
    if polarity < -TONE_THRESHOLD:
        return "negative"
    if polarity > TONE_THRESHOLD:
        return "positive"
    return "neutral"


def feedback_strength(content: str, attributes: Mapping[str, Any] | None = None) -> float:
    """How strongly it was said, 0..1: a detractor's or promoter's score is as strong as
    feedback gets; words are as strong as their sentiment."""
    band = feedback_band(content, attributes)
    if band in ("detractor", "promoter"):
        return 1.0
    if band == "passive":
        return 0.0
    return min(1.0, abs(feedback_polarity(content, attributes)))

