"""What a customer has asked *not* to receive — deterministically, from cue phrases.

"Please don't call me, email is fine" is a preference, and the channel extractor already
reads "email" out of it. What it cannot read is the *refusal*: that phone is ruled out, not
merely unmentioned. Guardrails need exactly that (§26 4.5) — an agent about to phone this
customer, or to pitch them an upgrade after "no sales calls", should be stopped — so
opt-outs are their own small closed set of kinds, the same shape as intents (§27.1).

Cues are matched as contiguous tokens after the same tokenisation every other lexicon uses,
so "don't call" and "dont call" are one cue, and "don't mind calls" is not an opt-out.
"""

from __future__ import annotations

from nlp.tokenize import tokenize

# Order is the order kinds are reported in.
OPT_OUT_CUES: dict[str, tuple[str, ...]] = {
    # Everything: an agent should not reach out at all.
    "contact": (
        "do not contact", "dont contact", "stop contacting", "stop reaching out",
        "no more contact", "leave us alone", "leave me alone", "remove me from your list",
        "remove us from your list", "take me off your list", "take us off your list",
        "no follow ups", "no more follow ups", "stop following up",
    ),
    "phone": (
        "do not call", "dont call", "stop calling", "no calls", "no phone calls",
        "never call", "no cold calls", "not by phone", "no more calls",
    ),
    "email": (
        "do not email", "dont email", "stop emailing", "no more emails", "no emails",
        "stop sending emails", "stop sending me emails", "not by email",
    ),
    "sms": ("do not text", "dont text", "stop texting", "no texts", "no sms", "stop sms"),
    "whatsapp": (
        "no whatsapp", "not on whatsapp", "not via whatsapp", "stop whatsapp",
        "dont whatsapp", "do not whatsapp",
    ),
    # Content rather than channel: no selling, no marketing.
    "sales": (
        "no sales calls", "no sales", "no upsell", "no upsells", "stop upselling",
        "stop trying to sell", "stop selling", "stop pitching", "no sales pitch",
        "not interested in upgrading", "not interested in an upgrade", "dont want to upgrade",
        "do not want to upgrade",
    ),
    "marketing": (
        "no marketing", "no newsletters", "no newsletter", "unsubscribe", "stop the newsletter",
        "no promotions", "no promotional", "opt out of marketing", "opted out of marketing",
        "stop marketing", "no marketing emails",
    ),
}

KINDS: tuple[str, ...] = tuple(OPT_OUT_CUES)

_COMPILED: dict[str, tuple[tuple[str, ...], ...]] = {
    kind: tuple(tuple(tokenize(cue)) for cue in cues) for kind, cues in OPT_OUT_CUES.items()
}


def _contains(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    width = len(phrase)
    if not width or width > len(tokens):
        return False
    return any(tuple(tokens[start : start + width]) == phrase for start in range(len(tokens) - width + 1))


def opt_outs(text: str) -> list[str]:
    """The opt-out kinds ``text`` expresses, in :data:`KINDS` order."""
    tokens = tokenize(text or "")
    if not tokens:
        return []
    return [kind for kind, phrases in _COMPILED.items() if any(_contains(tokens, phrase) for phrase in phrases)]


# What each kind rules out, for guardrails: a channel opt-out rules out contact on that
# channel; "contact" rules out every channel; "sales" and "marketing" rule out content.
CHANNEL_KINDS = frozenset({"phone", "email", "sms", "whatsapp"})
