"""Rewriting a customer's own words into a third-person memory statement.

A memory has to read as a statement *about* the customer ("The customer cannot connect
Shopify"), not as a quote ("I can't connect Shopify"). The rewriter is deliberately
conservative: when it cannot produce a grammatical sentence it falls back to an attributed
quote rather than inventing one, so a memory is never subtly wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nlp.lexicon import IRREGULAR_PAST, PRONOUN_REWRITES, VERB_AGREEMENT
from nlp.tokenize import expand_contractions, normalize

_FIRST_PERSON = re.compile(r"\b(i|we|my|our|mine|ours|us|me)\b", re.I)
# "the customer" where it is the subject: at the start, or after a clause boundary — a
# comma, or a conjunction. After a verb it is an object ("help the customer plan"), and the
# verb that follows is a bare infinitive that must stay as it is.
_SUBJECT = re.compile(
    r"(?:^|(?<=[,;:])\s*|\b(?:and|but|so|because|if|when|whenever|since|as|although|though|"
    r"while|until|unless|then|now|that|where|once)\s+)"
    r"(the customer)\s+(?:(also|really|still|just|now|usually|always|often|only|already|both|all|"
    r"currently|mostly|never|sometimes|actually|definitely|mainly|primarily|regularly|rarely|"
    r"typically|generally|normally)\s+)?([a-z]+)\b",
    re.I,
)
# Words after a subject that are never a base-form verb to conjugate.
_NOT_VERBS = frozenset({
    "is", "was", "has", "had", "will", "would", "can", "cannot", "could", "should", "shall",
    "may", "might", "must", "does", "did", "not", "and", "or", "but", "who", "which", "that",
    "the", "a", "an", "each", "team", "itself", "too", "as", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "about", "again", "here", "there", "then",
})
_IRREGULAR_THIRD = {"have": "has", "do": "does", "go": "goes", "are": "is", "were": "was", "be": "is"}
_SENTENCE_END = re.compile(r"[.!?]$")
_MULTI_SPACE = re.compile(r"\s{2,}")


@dataclass(slots=True, frozen=True)
class Rewritten:
    text: str
    quoted: bool
    changed: bool

    @property
    def is_faithful_paraphrase(self) -> bool:
        return not self.quoted


# Possessives and objects, rewritten in one pass left to right, because what they become
# depends on what came before: the first mention names the customer, later ones refer back —
# "we want to migrate our list" is "the customer wants to migrate their list", not "…the
# customer's list". Subjects always name the customer, so a verb's agreement never depends
# on a "they".
_POSSESSIVE_SOURCES = frozenset({"my ", "our ", "mine", "ours"})
_REMAINING = re.compile(r"(?<![\w'])(i|we|my|our|mine|ours|us|me)(?![\w'])", re.IGNORECASE)
_FIRST = {"my": "the customer's", "our": "the customer's", "mine": "the customer's", "ours": "the customer's",
          "us": "the customer", "me": "the customer"}
_LATER = {"my": "their", "our": "their", "mine": "theirs", "ours": "theirs", "us": "them", "me": "them"}


def _apply_pronouns(text: str) -> tuple[str, bool]:
    lowered = f" {text} "  # padded so word-boundary rules fire at both ends
    changed = False
    for source, target in PRONOUN_REWRITES:
        if source in _POSSESSIVE_SOURCES:
            continue
        pattern = re.compile(rf"(?<![\w']){re.escape(source)}", re.IGNORECASE)
        if pattern.search(lowered):
            lowered = pattern.sub(target, lowered)
            changed = True

    named = False

    def rewrite(match: re.Match[str]) -> str:
        nonlocal named
        word = match.group(1).lower()
        before = named or "the customer" in match.string[: match.start()]
        named = True
        if word in ("i", "we"):
            return "the customer"
        return (_LATER if before else _FIRST)[word]

    rewritten = _REMAINING.sub(rewrite, lowered)
    changed = changed or rewritten != lowered
    return _MULTI_SPACE.sub(" ", rewritten).strip(), changed


def third_person(verb: str) -> str | None:
    """The -s form of a base-form verb, or None when ``verb`` is not one to conjugate.

    Conservative: past tenses, gerunds and words already ending in -s are left alone
    ("uses" is already agreed; "process" is in the table), as is anything short enough to
    be a particle.
    """
    word = verb.lower()
    if word in VERB_AGREEMENT:
        return VERB_AGREEMENT[word]
    if word in _IRREGULAR_THIRD:
        return _IRREGULAR_THIRD[word]
    if word in IRREGULAR_PAST or word in _NOT_VERBS or len(word) < 3 or word.endswith(("ed", "ing", "s", "ly")):
        return None
    if word.endswith(("sh", "ch", "x", "z", "o")):
        return word + "es"
    if word.endswith("y") and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _fix_agreement(text: str) -> str:
    """"The customer want" → "The customer wants", wherever "the customer" is the subject:
    "Please do not call, the customer prefer email" → "…, the customer prefers email"."""

    def agree(match: re.Match[str]) -> str:
        verb = match.group(3)
        fixed = third_person(verb)
        if fixed is None:
            return match.group(0)
        if verb[:1].isupper():
            fixed = fixed[:1].upper() + fixed[1:]
        start, end = match.span(3)
        whole_start = match.start(0)
        return match.group(0)[: start - whole_start] + fixed + match.group(0)[end - whole_start :]

    return _SUBJECT.sub(agree, text)


def _capitalise(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def to_third_person(sentence: str) -> Rewritten:
    """Rewrite a first-person sentence; fall back to an attributed quote."""
    original = normalize(sentence).strip(" \"'")
    if not original:
        return Rewritten(text="", quoted=False, changed=False)

    if not _FIRST_PERSON.search(original):
        # Already third person ("Shopify sync keeps failing").
        text = _capitalise(original)
        if not _SENTENCE_END.search(text):
            text += "."
        return Rewritten(text=text, quoted=False, changed=False)

    expanded = expand_contractions(original)
    rewritten, changed = _apply_pronouns(expanded)

    if not changed or _FIRST_PERSON.search(rewritten):
        # Something first-person survived: quote instead of guessing.
        quote = original if _SENTENCE_END.search(original) else f"{original}."
        return Rewritten(text=f'Customer said: "{quote}"', quoted=True, changed=False)

    rewritten = _fix_agreement(rewritten)
    text = _capitalise(rewritten)
    if not _SENTENCE_END.search(text):
        text += "."
    return Rewritten(text=text, quoted=False, changed=True)


def as_statement(sentence: str, *, subject: str = "The customer") -> str:
    """Public helper: a memory-ready sentence for any input."""
    result = to_third_person(sentence)
    if subject != "The customer" and not result.quoted:
        return result.text.replace("The customer", subject, 1)
    return result.text
