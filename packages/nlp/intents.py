"""What a customer is trying to do, as a small closed set of kinds.

An intent memory says "the customer wants to add twelve seats before the rollout". That is
useful to a human and useless to a rule, which needs to ask "is this customer expanding?"
without parsing prose. So intent text is classified into kinds — deterministically, from
cue phrases, with negation respected — and the kinds are what conditions, guardrails and
"what changed?" read.

The kinds are deliberately few. A rule author has to be able to hold the whole list in
their head, and every extra kind is another way for two similar statements to land in
different buckets.
"""

from __future__ import annotations

from nlp.lexicon import NEGATION_STOPWORDS, NEGATIONS
from nlp.tokenize import lemmatize, tokenize

# Order matters only for the tie-break in ``primary_kind``: when a statement matches two
# kinds, the one listed first is the one a person would act on first.
INTENT_CUES: dict[str, tuple[str, ...]] = {
    "cancellation": (
        "cancel", "cancellation", "terminate", "churn", "close our account",
        "close the account", "stop using", "leave", "leaving", "not renew", "wont renew",
        "switch to", "switching to", "move to a competitor", "competitor", "refund",
    ),
    "downgrade": (
        "downgrade", "cheaper plan", "smaller plan", "reduce seats", "fewer seats",
        "remove seats", "cut back", "lower tier",
    ),
    "expansion": (
        "upgrade", "more seats", "add seats", "add users", "additional seats", "expand",
        "expansion", "enterprise plan", "roll out", "rollout", "company wide", "whole company",
        "entire company", "another team", "more licenses", "scale up",
    ),
    "evaluation": (
        "evaluate", "evaluating", "evaluation", "trial", "pilot", "proof of concept", "poc",
        "compare", "comparing", "demo", "considering", "shortlist",
    ),
    "integration": (
        "integrate", "integration", "connect", "connector", "api", "webhook", "sso", "saml",
        "single sign on",
    ),
    "migration": (
        "migrate", "migration", "move from", "moving from", "switch from", "switching from",
        "import our", "bring over", "move our data",
    ),
    "renewal": ("renew", "renewal", "annual contract", "extend the contract", "extend our contract"),
    "purchase": ("buy", "purchase", "quote", "pricing", "procurement", "purchase order"),
}

KINDS: tuple[str, ...] = tuple(INTENT_CUES)

# How far back a negation reaches. "we are not planning to cancel" negates; "no, we will
# cancel" — where the negation answers something else — mostly does not, and three tokens
# is where that line usually falls.
NEGATION_WINDOW = 3


def _normalise(phrase: str) -> tuple[str, ...]:
    return tuple(lemmatize(token) for token in tokenize(phrase))


# Precompiled once: each cue as a tuple of lemmas.
_CUES: dict[str, tuple[tuple[str, ...], ...]] = {
    kind: tuple(_normalise(cue) for cue in cues) for kind, cues in INTENT_CUES.items()
}


def _negated(tokens: list[str], start: int) -> bool:
    """Whether the cue starting at ``start`` sits inside a negation's reach."""
    for index in range(max(0, start - NEGATION_WINDOW), start):
        token = tokens[index]
        if token in NEGATION_STOPWORDS:
            # "not happy, but we will renew" — the clause break ends the negation.
            return False
        if token in NEGATIONS:
            return True
    return False


def intent_kinds(text: str) -> list[str]:
    """Every intent kind the text expresses, in ``KINDS`` order.

    Matching is on lemmas, so "cancelling", "cancelled" and "cancellation" all reach the
    same cue, and a cue inside a negation's reach does not count.
    """
    raw = tokenize(text)
    lemmas = [lemmatize(token) for token in raw]
    found: list[str] = []
    for kind, cues in _CUES.items():
        for cue in cues:
            width = len(cue)
            if not width:
                continue
            for start in range(len(lemmas) - width + 1):
                if tuple(lemmas[start : start + width]) == cue and not _negated(raw, start):
                    found.append(kind)
                    break
            if kind in found:
                break
    return found


def primary_kind(text: str) -> str | None:
    """The single kind to act on, when a caller needs one."""
    kinds = intent_kinds(text)
    return kinds[0] if kinds else None
