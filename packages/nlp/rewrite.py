"""Rewriting a customer's own words into a third-person memory statement.

A memory has to read as a statement *about* the customer ("The customer cannot connect
Shopify"), not as a quote ("I can't connect Shopify"). The rewriter is deliberately
conservative: when it cannot produce a grammatical sentence it falls back to an attributed
quote rather than inventing one, so a memory is never subtly wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nlp.lexicon import PRONOUN_REWRITES, VERB_AGREEMENT
from nlp.tokenize import expand_contractions, normalize

_FIRST_PERSON = re.compile(r"\b(i|we|my|our|mine|ours|us|me)\b", re.I)
_SUBJECT_START = re.compile(r"^(?:the customer)\s+([a-z']+)", re.I)
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


def _apply_pronouns(text: str) -> tuple[str, bool]:
    lowered = f" {text} "  # padded so word-boundary rules fire at both ends
    changed = False
    for source, target in PRONOUN_REWRITES:
        pattern = re.compile(rf"(?<![\w']){re.escape(source)}", re.IGNORECASE)
        if pattern.search(lowered):
            lowered = pattern.sub(target, lowered)
            changed = True
    # Bare subject pronouns that were not part of a longer phrase.
    for pronoun in ("i", "we"):
        pattern = re.compile(rf"(?<![\w']){pronoun}(?![\w'])", re.IGNORECASE)
        if pattern.search(lowered):
            lowered = pattern.sub("the customer", lowered)
            changed = True
    for pronoun, replacement in (("us", "the customer"), ("me", "the customer")):
        pattern = re.compile(rf"(?<![\w']){pronoun}(?![\w'])", re.IGNORECASE)
        if pattern.search(lowered):
            lowered = pattern.sub(replacement, lowered)
            changed = True
    return _MULTI_SPACE.sub(" ", lowered).strip(), changed


def _fix_agreement(text: str) -> str:
    """"The customer want" → "The customer wants"."""
    match = _SUBJECT_START.match(text)
    if not match:
        return text
    verb = match.group(1).lower()
    replacement = VERB_AGREEMENT.get(verb)
    if not replacement:
        return text
    start, end = match.span(1)
    return text[:start] + replacement + text[end:]


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
