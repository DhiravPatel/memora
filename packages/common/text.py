"""Small text utilities used by extraction, deduplication and keyword search."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WORD_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "did", "do", "does", "for", "from", "had", "has", "have", "he", "her", "his", "how", "i", "if", "in", "is", "it", "its", "me", "my", "not", "of", "on", "or", "our", "she", "so", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "you", "your"]
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    """Words, lowercased. Apostrophes are dropped so "customer's" matches "customer"."""
    return [token for token in _WORD_RE.findall(text.lower().replace("'s ", " ")) if token != "s"]


def keywords(text: str, limit: int = 12) -> list[str]:
    seen: dict[str, int] = {}
    for token in tokenize(text):
        if len(token) < 3 or token in STOPWORDS:
            continue
        seen[token] = seen.get(token, 0) + 1
    ranked = sorted(seen.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).lower().encode("utf-8")).hexdigest()


def jaccard(a: str, b: str) -> float:
    left, right = set(tokenize(a)) - STOPWORDS, set(tokenize(b)) - STOPWORDS
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
