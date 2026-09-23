"""Deterministic local embeddings.

No model, no network, no API key: a memory's vector is a signed hashing projection of its
lemmatised words, word bigrams and character n-grams, weighted so that domain terms
dominate and boilerplate does not. The same text always produces the same vector, on every
machine and forever, which is what makes stored vectors safe to compare years later.

Trade-off, stated plainly: this captures *lexical* similarity (shared words, morphology,
typos) but not paraphrase without shared words. The retrieval engine compensates by
blending four other strategies — keyword, temporal, entity and relationship — so a missed
semantic match is not a missed memory.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from common.settings import Settings, get_settings
from common.text import STOPWORDS
from nlp.lexicon import INTEGRATIONS, PLANS
from nlp.tokenize import char_ngrams, correct_spelling, lemmas, lemmatize, ngrams, normalize

MODEL_NAME = "memora-lexical-v1"

# Words that appear in nearly every support message carry little signal.
_LOW_INFORMATION = frozenset(
    {
        "customer", "account", "use", "using", "get", "make", "need", "help", "please", "thank",
        "hello", "team", "support", "time", "day", "week", "month", "year", "work", "thing",
        "would", "could", "also", "still", "back", "want", "know", "see", "tell", "let", "one",
        "way", "new", "now", "just", "like", "good", "issue",
    }
)

_DOMAIN_TERMS = frozenset(
    {term.lower() for term in INTEGRATIONS.values()}
    | {term.lower() for term in PLANS.values()}
    | set(INTEGRATIONS)
    | set(PLANS)
)

# Feature family weights. Unigrams carry the meaning; bigrams capture short phrases;
# character n-grams give robustness to typos and inflections.
WEIGHT_UNIGRAM = 1.0
WEIGHT_BIGRAM = 0.55
WEIGHT_CHAR = 0.22
WEIGHT_DOMAIN_BOOST = 1.8
WEIGHT_LOW_INFORMATION = 0.35


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int

    @property
    def first(self) -> list[float]:
        return self.vectors[0]


def _feature_weight(token: str) -> float:
    if token in _DOMAIN_TERMS:
        return WEIGHT_UNIGRAM * WEIGHT_DOMAIN_BOOST
    if token in _LOW_INFORMATION or token in STOPWORDS:
        return WEIGHT_LOW_INFORMATION
    # Longer, rarer words carry more signal than short common ones.
    return WEIGHT_UNIGRAM * (1.0 + min(0.3, max(0, len(token) - 5) * 0.06))


def _hash(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:4], "big") % dimensions
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return index, sign


def features(text: str) -> Counter[str]:
    """The weighted feature bag for a text, exposed for debugging and tests."""
    normalised = correct_spelling(normalize(text)).lower()
    tokens = [token for token in lemmas(normalised) if token]
    content = [token for token in tokens if token not in STOPWORDS and len(token) > 1]

    bag: Counter[str] = Counter()
    for token in content:
        bag[f"w:{token}"] += 1
    for bigram in ngrams(content, 2):
        bag[f"b:{bigram}"] += 1
    for gram in char_ngrams(" ".join(content), 4):
        bag[f"c:{gram}"] += 1
    return bag


def embed_text(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    bag = features(text)
    if not bag:
        bag = Counter({"w:__empty__": 1})

    for feature, count in bag.items():
        kind, _, value = feature.partition(":")
        if kind == "w":
            weight = _feature_weight(value)
        elif kind == "b":
            weight = WEIGHT_BIGRAM
        else:
            weight = WEIGHT_CHAR
        # Sublinear term frequency: the fifth mention of a word is not five times the signal.
        weight *= 1.0 + math.log(count)
        index, sign = _hash(feature, dimensions)
        vector[index] += sign * weight

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class LocalEmbedder:
    """The embedding provider used everywhere in the system."""

    name = "local-lexical"

    def __init__(self, dimensions: int | None = None, settings: Settings | None = None) -> None:
        resolved = settings or get_settings()
        self.dimensions = dimensions or resolved.embedding_dimensions
        self.model = f"{MODEL_NAME}-{self.dimensions}"

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[embed_text(text, self.dimensions) for text in texts],
            model=self.model,
            dimensions=self.dimensions,
        )

    async def embed_one(self, text: str) -> list[float]:
        return embed_text(text, self.dimensions)

    def embed_sync(self, text: str) -> list[float]:
        """Synchronous variant for scripts and offline tooling."""
        return embed_text(text, self.dimensions)

    async def aclose(self) -> None:  # parity with network-backed providers
        return None


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, dot))


def most_similar(
    query_vector: Sequence[float], candidates: Iterable[tuple[str, Sequence[float]]], limit: int = 5
) -> list[tuple[str, float]]:
    scored = [(key, cosine(query_vector, vector)) for key, vector in candidates]
    scored.sort(key=lambda item: -item[1])
    return scored[:limit]


def keyword_vector(terms: Sequence[str], dimensions: int) -> list[float]:
    """Embed a bare list of terms (used for query expansion)."""
    return embed_text(" ".join(lemmatize(term) for term in terms), dimensions)
