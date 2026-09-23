"""Tokenisation, sentence splitting and a lightweight lemmatiser.

No model files, no downloads, no network: everything is rule-based so extraction is
identical on every machine and in every environment, and a given input always yields the
same memory.
"""

from __future__ import annotations

import re
import unicodedata

from common.text import STOPWORDS
from nlp.lexicon import CONTRACTIONS, SPELLING_FIXES

# Abbreviations that must not end a sentence.
_ABBREVIATIONS = (
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc",
    "ltd", "co", "no", "fig", "approx", "dept", "est", "min", "max", "sec",
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])[\s\n]+(?=[\"'(\[]?[A-Z0-9])")
_LINE_BOUNDARY = re.compile(r"[\n\r]+")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_WHITESPACE = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+|www\.\S+")

# Suffix rules, longest first. Deliberately conservative: over-stemming destroys meaning
# ("billing" -> "bil"), so only endings that reliably preserve the root are stripped.
_SUFFIX_RULES: tuple[tuple[str, str, int], ...] = (
    ("ingly", "", 5), ("edly", "", 5), ("ities", "ity", 5), ("ations", "ation", 6),
    ("ements", "ement", 6), ("ements", "ement", 6), ("ness", "", 5), ("ments", "ment", 6),
    ("ing", "", 5), ("ied", "y", 4), ("ies", "y", 4), ("ees", "ee", 4), ("ves", "f", 4),
    ("ses", "s", 4), ("xes", "x", 4), ("zes", "z", 4), ("ches", "ch", 5), ("shes", "sh", 5),
    ("ed", "", 4), ("s", "", 3),
)

# Short verbs whose inflections the suffix rules cannot reach consistently.
_SHORT_VERBS: dict[str, str] = {
    "using": "use", "used": "use", "uses": "use", "made": "make", "making": "make",
    "makes": "make", "given": "give", "giving": "give", "gave": "give", "taken": "take",
    "taking": "take", "took": "take", "coming": "come", "came": "come", "having": "have",
    "doing": "do", "done": "do", "seen": "see", "saw": "see", "seeing": "see",
}

_IRREGULAR: dict[str, str] = {
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be", "been": "be", "being": "be",
    "has": "have", "had": "have", "having": "have", "does": "do", "did": "do", "doing": "do",
    "went": "go", "gone": "go", "goes": "go", "lost": "lose", "losing": "lose",
    "paid": "pay", "paying": "pay", "left": "leave", "leaving": "leave", "kept": "keep",
    "felt": "feel", "thought": "think", "bought": "buy", "brought": "bring", "sent": "send",
    "spent": "spend", "built": "build", "broke": "break", "broken": "break", "ran": "run",
    "tried": "try", "trying": "try", "tries": "try", "said": "say", "saying": "say",
    "children": "child", "people": "person", "data": "data",
    # Doubled-consonant inflections the suffix rules deliberately leave alone.
    "cancelled": "cancel", "canceled": "cancel", "cancelling": "cancel", "canceling": "cancel",
    "cancellation": "cancel", "cancellations": "cancel", "billed": "bill", "billing": "billing",
    "installed": "install", "installing": "install", "enrolled": "enrol", "refunded": "refund",
}

# Words that must never be stemmed (stemming would merge distinct meanings).
_PROTECTED = frozenset(
    {"billing", "pricing", "shipping", "marketing", "onboarding", "reporting", "settings",
     "analytics", "business", "address", "success", "access", "process", "status", "sales",
     "class", "less", "press", "gross", "loss", "boss", "miss", "pass", "cross", "always",
     "is", "as", "has", "was", "this", "us", "plus", "news", "series", "species"}
)


_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n\r]+")
_CONTRACTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(key) for key in sorted(CONTRACTIONS, key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Unicode-normalise, collapse whitespace and strip URLs."""
    text = unicodedata.normalize("NFKC", str(text))
    text = _URL.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_lines(text: str) -> str:
    """Like :func:`normalize` but keeps line breaks, which mark sentence boundaries."""
    text = unicodedata.normalize("NFKC", str(text))
    text = _URL.sub(" ", text)
    return _HORIZONTAL_WHITESPACE.sub(" ", text).strip()


def expand_contractions(text: str) -> str:
    """"doesn't work" -> "does not work", so negation and cue phrases match reliably.

    Casing is preserved: the rewriter needs "Shopify" to stay capitalised.
    """

    def replace(match: re.Match[str]) -> str:
        expansion = CONTRACTIONS[match.group(0).lower()]
        return expansion.capitalize() if match.group(0)[:1].isupper() else expansion

    return _CONTRACTION_RE.sub(replace, text)


def correct_spelling(text: str) -> str:
    """Fix a small set of common misspellings so they match the lexicons."""
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        fixed = SPELLING_FIXES.get(word.lower())
        if fixed is None:
            return word
        return fixed.capitalize() if word[:1].isupper() else fixed

    return re.sub(r"[A-Za-z']+", replace, text)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, respecting newlines, bullets and abbreviations."""
    text = normalize_lines(text)
    if not text:
        return []

    sentences: list[str] = []
    for line in _LINE_BOUNDARY.split(text):
        line = line.strip(" -*•\t")
        if not line:
            continue
        parts = _SENTENCE_BOUNDARY.split(line)
        buffer = ""
        for part in parts:
            candidate = f"{buffer} {part}".strip() if buffer else part.strip()
            last_word = candidate.rstrip(".").split(" ")[-1].lower() if candidate else ""
            if last_word in _ABBREVIATIONS:
                buffer = candidate
                continue
            buffer = ""
            if candidate:
                sentences.append(candidate)
        if buffer:
            sentences.append(buffer)
    return sentences


def split_clauses(sentence: str) -> list[str]:
    """Split on contrastive conjunctions so "X works but Y fails" yields two statements."""
    parts = re.split(r",?\s+(?:but|however|although|though|whereas)\s+", sentence, flags=re.I)
    return [part.strip() for part in parts if part.strip()]


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; apostrophes are folded so "customer's" matches "customer"."""
    lowered = text.lower().replace("’", "'")
    return [token.replace("'s", "").replace("'", "") for token in _WORD.findall(lowered) if token]


def _canonical(stem: str) -> str:
    """Fold a trailing silent "e" so "upgrade" and "upgraded" reduce to the same stem.

    Consistency matters more than prettiness here: these stems are only ever compared
    with each other, never shown to a user.
    """
    if len(stem) > 4 and stem.endswith("e") and not stem.endswith(("ee", "ie", "oe")):
        return stem[:-1]
    return stem


def lemmatize(token: str) -> str:
    """Reduce a token towards its root using conservative suffix rules."""
    token = token.lower()
    if token in _SHORT_VERBS:
        return _SHORT_VERBS[token]
    if token in _IRREGULAR:
        return _IRREGULAR[token]
    if token in _PROTECTED or len(token) <= 3:
        return token
    token = _canonical(token)
    if token in _PROTECTED or len(token) <= 3:
        return token
    # "boxes"/"watches" keep their stem-final consonant cluster; "times"/"charges" do not.
    if token.endswith("es") and len(token) > 4 and token[-3] in "sxzhoi":
        stem = token[:-2]
        if len(stem) >= 3:
            return _canonical(stem)
    for suffix, replacement, minimum_length in _SUFFIX_RULES:
        if token.endswith(suffix) and len(token) >= minimum_length:
            stem = token[: -len(suffix)] + replacement
            if len(stem) >= 3:
                # Undo doubled consonants: "stopping" -> "stopp" -> "stop".
                if len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "sl":
                    stem = stem[:-1]
                return _canonical(stem)
    return token


def lemmas(text: str, *, drop_stopwords: bool = False) -> list[str]:
    tokens = [lemmatize(token) for token in tokenize(expand_contractions(text))]
    if drop_stopwords:
        return [token for token in tokens if token not in STOPWORDS and len(token) > 1]
    return tokens


def lemmatized_text(text: str) -> str:
    """A lemmatised rendering used for cue-phrase matching."""
    return " ".join(lemmas(text))


def ngrams(tokens: list[str], size: int) -> list[str]:
    if size <= 1:
        return list(tokens)
    return [" ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)]


def char_ngrams(text: str, size: int = 4) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text.lower())
    if len(cleaned) < size:
        return [cleaned] if cleaned else []
    return [cleaned[index : index + size] for index in range(len(cleaned) - size + 1)]


def content_words(text: str) -> list[str]:
    """Lemmatised, stopword-free tokens: the words that carry meaning."""
    return lemmas(text, drop_stopwords=True)


def is_question(sentence: str) -> bool:
    stripped = sentence.strip()
    if stripped.endswith("?"):
        return True
    first = tokenize(stripped)[:1]
    return bool(first) and first[0] in {
        "what", "why", "when", "where", "who", "how", "is", "are", "can", "could", "do", "does",
        "did", "should", "would", "will",
    }


def word_count(text: str) -> int:
    return len(tokenize(text))
