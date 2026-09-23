"""Extractive summarisation and diversity selection.

Summaries are built by *selecting* sentences the customer or the system already wrote,
never by generating new ones. A summary can therefore be traced to its sources, and it can
never assert something no event supports.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from common.text import STOPWORDS
from nlp.embeddings import cosine, embed_text
from nlp.tokenize import content_words, split_sentences


@dataclass(slots=True)
class ScoredSentence:
    text: str
    score: float
    index: int


def key_phrases(texts: Sequence[str], *, limit: int = 8) -> list[tuple[str, int]]:
    """Most frequent meaningful terms across a set of texts."""
    counter: Counter[str] = Counter()
    documents: Counter[str] = Counter()
    for text in texts:
        words = [word for word in content_words(text) if len(word) >= 3 and word not in STOPWORDS]
        counter.update(words)
        documents.update(set(words))
    # A term mentioned in several memories matters more than one repeated in a single rant.
    ranked = sorted(
        counter.items(), key=lambda item: (-(documents[item[0]] * 2 + item[1]), item[0])
    )
    return [(word, counter[word]) for word, _ in ranked[:limit]]


def rank_sentences(text: str, *, limit: int = 3) -> list[ScoredSentence]:
    """Classic centroid scoring: sentences closest to the document's own centre."""
    seen: set[str] = set()
    sentences: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence.split()) < 4:
            continue
        key = " ".join(content_words(sentence))
        if key in seen:
            continue
        seen.add(key)
        sentences.append(sentence)
    if not sentences:
        return []

    dimensions = 256
    vectors = [embed_text(sentence, dimensions) for sentence in sentences]
    centroid = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]

    scored = [
        ScoredSentence(text=sentence, score=cosine(centroid, vector), index=index)
        for index, (sentence, vector) in enumerate(zip(sentences, vectors, strict=True))
    ]
    scored.sort(key=lambda item: -item.score)
    return scored[:limit]


def summarize(text: str, *, max_sentences: int = 3) -> str:
    selected = rank_sentences(text, limit=max_sentences)
    selected.sort(key=lambda item: item.index)  # keep original order for readability
    return " ".join(item.text for item in selected)


def mmr_select(
    items: Sequence[object],
    *,
    text_of: Callable[[object], str],
    score_of: Callable[[object], float],
    limit: int,
    diversity: float = 0.35,
    dimensions: int = 256,
) -> list[object]:
    """Maximal Marginal Relevance.

    Picks the highest-scoring item, then repeatedly picks the item that maximises
    ``(1 - diversity) * relevance - diversity * similarity_to_already_picked``. Without
    this, five phrasings of the same problem crowd out everything else in a context window.
    """
    if limit <= 0 or not items:
        return []
    pool = list(items)
    vectors = {id(item): embed_text(text_of(item), dimensions) for item in pool}
    scores = {id(item): score_of(item) for item in pool}

    selected: list[object] = []
    while pool and len(selected) < limit:
        best_item = None
        best_value = float("-inf")
        for item in pool:
            relevance = scores[id(item)]
            redundancy = max(
                (cosine(vectors[id(item)], vectors[id(chosen)]) for chosen in selected),
                default=0.0,
            )
            value = (1 - diversity) * relevance - diversity * redundancy
            if value > best_value:
                best_item, best_value = item, value
        if best_item is None:
            break
        selected.append(best_item)
        pool.remove(best_item)
    return selected


def deduplicate(texts: Sequence[str], *, threshold: float = 0.85, dimensions: int = 256) -> list[str]:
    """Drop near-identical texts, keeping the first occurrence."""
    kept: list[str] = []
    vectors: list[list[float]] = []
    for text in texts:
        vector = embed_text(text, dimensions)
        if any(cosine(vector, existing) >= threshold for existing in vectors):
            continue
        kept.append(text)
        vectors.append(vector)
    return kept
