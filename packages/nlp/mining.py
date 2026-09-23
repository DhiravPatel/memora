"""Mining a project's own vocabulary.

The shipped synonym list knows that "billing" relates to "invoice". It cannot know that in
*your* product "sync" and "handoff" are the same thing, or that your customers call the
importer "the loader". That gap is the single biggest cause of a missed retrieval: a
paraphrase with no shared vocabulary does not match, however good the ranking is.

This module closes it without a model. Terms that keep appearing in the same memories, and
in the memories of the same customers, are related — and the standard measure of that is
pointwise mutual information: how much more often two terms occur together than chance
would predict. It is *normalised* PMI here, which divides out the corpus size and lands on
a bounded -1..1 scale, so one threshold behaves the same for a project with 200 memories
and one with two million. Raw PMI does not: its ceiling shrinks as a term gets commoner,
which made any single threshold either unreachable on a small corpus or meaningless on a
large one. The arithmetic is over plain counts, so a result is reproducible, inspectable,
and explains itself in terms of how many memories support each pair.

What this deliberately is *not*: a similarity model. A pair is proposed only when the raw
co-occurrence count clears a floor, so one rambling support thread cannot teach the system
a word.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from common.text import STOPWORDS
from nlp.lexicon import SYNONYMS
from nlp.tokenize import content_words, lemmatize

# A pair needs this many distinct memories behind it before it is proposed. Three is the
# point where "these words go together" stops being an accident of one conversation.
MIN_SUPPORT = 3
# Below this, a term is too rare for its co-occurrence counts to mean anything.
MIN_TERM_FREQUENCY = 3
# Normalised PMI: 1.0 means the two terms only ever appear together, 0 means exactly what
# chance would predict. Half-way keeps pairs that travel together most of the time without
# demanding they be true synonyms.
MIN_SCORE = 0.5
MAX_PAIRS_PER_TERM = 4
MAX_PAIRS = 400
# Documents any one term may appear in before it is treated as filler for this project.
# A word in nearly every memory ("customer", "account") relates to everything and so
# discriminates nothing.
MAX_DOCUMENT_RATIO = 0.4
# Three, not four: "sso", "api", "csv" and "pdf" are exactly the product vocabulary this
# is meant to learn. Short filler words are already excluded as stopwords.
MIN_TERM_LENGTH = 3


@dataclass(slots=True, frozen=True)
class LearnedPair:
    """One mined relationship, with everything needed to argue with it."""

    term: str
    synonym: str
    score: float
    support: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "synonym": self.synonym,
            "score": round(self.score, 3),
            "support": self.support,
        }


def terms_of(text: str) -> list[str]:
    """The lemmas of a memory, deduplicated, with stopwords and short words dropped."""
    seen: dict[str, None] = {}
    for word in content_words(text):
        lemma = lemmatize(word)
        if len(lemma) < MIN_TERM_LENGTH or lemma in STOPWORDS or lemma.isdigit():
            continue
        seen.setdefault(lemma, None)
    return list(seen)


def mine(
    documents: Iterable[Sequence[str]],
    *,
    min_support: int = MIN_SUPPORT,
    min_score: float = MIN_SCORE,
    max_pairs: int = MAX_PAIRS,
) -> list[LearnedPair]:
    """Find term pairs that co-occur far more often than chance.

    ``documents`` is an iterable of already-tokenised term lists — one per memory. Keeping
    tokenisation outside makes this function pure arithmetic and trivial to test.
    """
    documents = [list(dict.fromkeys(document)) for document in documents]
    total = len(documents)
    if total < min_support:
        return []

    term_counts: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    for terms in documents:
        term_counts.update(terms)
        ordered = sorted(terms)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                pair_counts[(first, second)] += 1

    ceiling = max(min_support, int(total * MAX_DOCUMENT_RATIO))
    frequent = {
        term
        for term, count in term_counts.items()
        if MIN_TERM_FREQUENCY <= count <= ceiling
    }

    scored: list[LearnedPair] = []
    for (first, second), together in pair_counts.items():
        if together < min_support or first not in frequent or second not in frequent:
            continue
        # NPMI = log(P(a,b) / (P(a)·P(b))) / -log(P(a,b)).
        # The denominator is the most the numerator could have been, which is what makes
        # the result comparable between a small project and a large one.
        probability = together / total
        expected = (term_counts[first] / total) * (term_counts[second] / total)
        if expected <= 0 or probability >= 1.0:
            continue
        score = math.log(probability / expected) / -math.log(probability)
        if score < min_score:
            continue
        # Already in the shipped lexicon: learning it again adds nothing.
        if second in SYNONYMS.get(first, ()) or first in SYNONYMS.get(second, ()):
            continue
        scored.append(LearnedPair(term=first, synonym=second, score=score, support=together))

    # Strongest first, ties broken alphabetically so the output is stable.
    scored.sort(key=lambda pair: (-pair.score, -pair.support, pair.term, pair.synonym))
    return _cap_per_term(scored, max_pairs=max_pairs)


def _cap_per_term(pairs: list[LearnedPair], *, max_pairs: int) -> list[LearnedPair]:
    """Keep the strongest few per term, so one busy word cannot fill the table."""
    per_term: Counter[str] = Counter()
    kept: list[LearnedPair] = []
    for pair in pairs:
        if (
            per_term[pair.term] >= MAX_PAIRS_PER_TERM
            or per_term[pair.synonym] >= MAX_PAIRS_PER_TERM
        ):
            continue
        per_term[pair.term] += 1
        per_term[pair.synonym] += 1
        kept.append(pair)
        if len(kept) >= max_pairs:
            break
    return kept


def as_expansion_table(pairs: Iterable[LearnedPair]) -> dict[str, tuple[str, ...]]:
    """Fold mined pairs into the same shape as the shipped ``SYNONYMS`` table.

    Both directions are stored: if "sync" finds "handoff", then "handoff" finds "sync".
    """
    table: dict[str, list[str]] = {}
    for pair in pairs:
        table.setdefault(pair.term, []).append(pair.synonym)
        table.setdefault(pair.synonym, []).append(pair.term)
    return {term: tuple(dict.fromkeys(values)) for term, values in table.items()}
