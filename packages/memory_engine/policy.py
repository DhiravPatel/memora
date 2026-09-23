"""Who is allowed to see which memory.

PII redaction (``common/pii.py``) removes the things nobody should store: card numbers,
secrets, national identifiers. This module handles the different problem underneath it —
text that is *legitimately* stored and still must not be handed to everyone. A support
agent's key should not be able to read what a customer said about their health, their
legal position, or their salary, even though the memory is real and the answer engine
would happily cite it.

The rules are a project's own, written as settings, and evaluated deterministically:

* a **type** rule restricts a whole class of memory;
* a **term** rule restricts anything mentioning one of a list of words;
* a **pattern** rule restricts anything matching a regular expression.

A restricted memory is never silently dropped. Reads without clearance say how many were
withheld, and a masked memory keeps its shape — the reader knows something is there, which
is the honest behaviour and the one that stops people chasing a gap they cannot see.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from common.enums import MemoryType
from nlp.tokenize import content_words, lemmatize

# What a reader without clearance sees in place of the content.
MASK = "[restricted]"
MAX_RULES = 24
MAX_TERMS_PER_RULE = 60
# A pattern that takes this long is a denial of service, not a policy.
MAX_PATTERN_LENGTH = 200


@dataclass(slots=True, frozen=True)
class Rule:
    """One reason a memory might be restricted."""

    kind: str  # "type" | "term" | "pattern"
    value: str
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "label": self.label}


@dataclass(slots=True, frozen=True)
class Verdict:
    """The outcome for one memory, and the rule that produced it."""

    restricted: bool
    rule: Rule | None = None

    @property
    def reason(self) -> str | None:
        return self.rule.label if self.rule else None


class PolicyError(ValueError):
    """A policy that cannot be compiled — raised at write time, never at read time."""


@dataclass(slots=True)
class Policy:
    """A project's compiled restriction rules."""

    rules: tuple[Rule, ...] = ()
    _patterns: tuple[tuple[Rule, re.Pattern[str]], ...] = ()
    _terms: tuple[tuple[Rule, frozenset[str]], ...] = ()
    _types: tuple[tuple[Rule, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.rules

    def evaluate(self, *, content: str, memory_type: str) -> Verdict:
        """Whether this memory is restricted, and by which rule.

        Type rules are checked first because they are the cheapest and the broadest; terms
        next; patterns last, since a regular expression is the most expensive test.
        """
        for rule, wanted in self._types:
            if memory_type == wanted:
                return Verdict(True, rule)

        if self._terms:
            lemmas = {lemmatize(word) for word in content_words(content)}
            for rule, terms in self._terms:
                if lemmas & terms:
                    return Verdict(True, rule)

        for rule, pattern in self._patterns:
            if pattern.search(content):
                return Verdict(True, rule)

        return Verdict(False)


def _term_variants(word: str) -> set[str]:
    """Every stem a restricted word could plausibly appear as.

    The stemmer is not consistent across inflections — "salary" stems to ``salary`` but
    "salaries" stems to ``salari`` — so matching a single stem would let a rule written as
    "salary" sail past a memory that says "salaries". For a redaction gate that is the
    worst kind of bug, because it fails open and silently. So each written word is expanded
    into its ordinary English inflections first, and every resulting stem is matched.
    """
    forms = {word, f"{word}s", f"{word}es"}
    if word.endswith("y"):
        forms.add(f"{word[:-1]}ies")
    if word.endswith("s"):
        forms.add(word[:-1])
    return {lemmatize(form) for form in forms if form}


def compile_policy(raw: Sequence[Any] | None) -> Policy:
    """Turn the stored settings into something evaluable.

    Raises :class:`PolicyError` on anything malformed, so a bad rule is refused when it is
    written rather than crashing every read afterwards.
    """
    if not raw:
        return Policy()
    if not isinstance(raw, (list, tuple)):
        raise PolicyError("Restriction policies must be a list of rules.")
    if len(raw) > MAX_RULES:
        raise PolicyError(f"At most {MAX_RULES} restriction rules.")

    rules: list[Rule] = []
    patterns: list[tuple[Rule, re.Pattern[str]]] = []
    terms: list[tuple[Rule, frozenset[str]]] = []
    types: list[tuple[Rule, str]] = []

    for entry in raw:
        if not isinstance(entry, dict):
            raise PolicyError("Each restriction rule must be an object.")
        kind = str(entry.get("kind", "")).strip().lower()
        value = entry.get("value")
        label = str(entry.get("label") or "").strip()

        if kind == "type":
            name = str(value or "").strip().lower()
            if name not in {item.value for item in MemoryType}:
                raise PolicyError(f"'{name}' is not a memory type.")
            rule = Rule(kind="type", value=name, label=label or f"{name} memories are restricted")
            types.append((rule, name))

        elif kind == "term":
            words = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
            written = [str(word).strip().lower() for word in words if str(word).strip()]
            cleaned: set[str] = set()
            for word in written:
                cleaned |= _term_variants(word)
            if not cleaned:
                raise PolicyError("A term rule needs at least one word.")
            if len(written) > MAX_TERMS_PER_RULE:
                raise PolicyError(f"At most {MAX_TERMS_PER_RULE} words in one term rule.")
            rule = Rule(
                # The words as written, so the stored policy stays readable and round
                # trips; the expanded stems are an implementation detail of matching.
                kind="term",
                value=",".join(sorted(written)),
                label=label or "mentions a restricted term",
            )
            terms.append((rule, frozenset(cleaned)))

        elif kind == "pattern":
            expression = str(value or "").strip()
            if not expression:
                raise PolicyError("A pattern rule needs an expression.")
            if len(expression) > MAX_PATTERN_LENGTH:
                raise PolicyError(f"A pattern may be at most {MAX_PATTERN_LENGTH} characters.")
            try:
                compiled = re.compile(expression, re.IGNORECASE)
            except re.error as exc:
                raise PolicyError(f"'{expression}' is not a valid regular expression: {exc}") from exc
            rule = Rule(kind="pattern", value=expression, label=label or "matches a restricted pattern")
            patterns.append((rule, compiled))

        else:
            raise PolicyError(f"'{kind}' is not a rule kind. Use type, term or pattern.")

        rules.append(rule)

    return Policy(
        rules=tuple(rules),
        _patterns=tuple(patterns),
        _terms=tuple(terms),
        _types=tuple(types),
    )


def classify(
    project_settings: dict[str, Any] | None, *, content: str, memory_type: str
) -> tuple[str, str | None]:
    """Sensitivity for one memory, for the write paths outside the consolidator.

    Every route that creates a memory has to go through this — a manual write, a
    correction, an agent session summary — or the policy would only cover memories that
    happened to arrive as events, which is the kind of gap nobody notices until it is
    quoted back at them.

    Returns ``(sensitivity, reason)`` as plain strings so callers need not import the enum.
    A malformed stored policy classifies nothing rather than failing the write; settings
    validation is what stops one being stored.
    """
    try:
        compiled = compile_policy((project_settings or {}).get("restriction_policies"))
    except PolicyError:
        return "normal", None
    if compiled.is_empty:
        return "normal", None
    verdict = compiled.evaluate(content=content, memory_type=memory_type)
    return ("restricted" if verdict.restricted else "normal"), verdict.reason


def mask(content: str) -> str:
    """What a reader without clearance sees. Length is not leaked, only presence."""
    return MASK


def partition(
    memories: Iterable[Any], *, cleared: bool, sensitivity_of=lambda item: getattr(item, "sensitivity", "normal")
) -> tuple[list[Any], int]:
    """Split memories into what a reader may see and a count of what was withheld.

    Used on every read path. Returning the count rather than silently shortening the list
    is the whole point: a reader is told that something exists and that they cannot see it.
    """
    if cleared:
        return list(memories), 0
    allowed: list[Any] = []
    withheld = 0
    for memory in memories:
        if str(sensitivity_of(memory)) == "restricted":
            withheld += 1
        else:
            allowed.append(memory)
    return allowed, withheld
