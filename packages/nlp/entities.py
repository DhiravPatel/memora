"""Deterministic entity recognition.

Three complementary passes, strongest first:

1. **Gazetteer** — known integrations, plans and channels, matched on lemmatised text.
2. **Patterns** — ticket ids, order ids, money, dates, counts and versions.
3. **Proper-noun heuristics** — capitalised spans that survive a stoplist, typed by the
   words around them ("works at Acme" → company, "spoke to Priya" → person).

Each hit carries the rule that produced it, so the dashboard can explain why an entity
exists and an operator can correct the lexicon rather than the code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from common.enums import EntityType, RelationshipType
from nlp.lexicon import CHANNELS, INTEGRATIONS, PLANS, PROPER_NOUN_STOPLIST, ROLES
from nlp.tokenize import lemmatized_text, normalize, tokenize

TICKET_RE = re.compile(r"\b(?:ticket|case|issue|inc)\s*[#:]?\s*([A-Z]{0,4}-?\d{2,8})\b", re.I)
HASH_ID_RE = re.compile(r"(?<![\w/])#(\d{2,10})\b")
ORDER_RE = re.compile(r"\border\s*[#:]?\s*([A-Za-z0-9-]{3,20})\b", re.I)
INVOICE_RE = re.compile(r"\binvoice\s*[#:]?\s*([A-Za-z0-9-]{3,20})\b", re.I)
MONEY_RE = re.compile(r"(?:[$€£₹]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?(?:usd|eur|gbp|inr|dollars?|rupees?)\b)", re.I)
PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b",
    re.I,
)
COUNT_RE = re.compile(
    r"\b(\d{1,3}|once|twice|thrice|one|two|three|four|five|six|seven|eight|nine|ten)\s+times?\b",
    re.I,
)
PROPER_NOUN_RE = re.compile(r"\b([A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,3})\b")

_WORD_NUMBERS = {
    "once": 1, "one": 1, "twice": 2, "two": 2, "thrice": 3, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_COMPANY_MARKERS = ("work at", "works at", "work for", "works for", "at the company", "company", "based at")
_PERSON_MARKERS = (
    "spoke to", "spoke with", "talked to", "met with", "my colleague", "colleague", "opened by",
    "raised by", "assigned to", "replied by", "answered by", "emailed", "called", "mr", "ms",
    "contacted by", "handled by",
)
# Domain nouns that are capitalised in support text but are not entities of their own.
_DOMAIN_STOPLIST = frozenset(
    {"ticket", "tickets", "order", "orders", "invoice", "invoices", "plan", "plans", "account",
     "accounts", "subscription", "billing", "support", "dashboard", "integration", "integrations",
     "team", "sync", "error", "issue", "problem", "update", "settings", "report", "reports",
     "api", "sdk", "app", "web", "beta", "urgent", "note", "thanks", "hello", "hi"}
)
_COMPANY_SUFFIXES = ("inc", "ltd", "llc", "gmbh", "plc", "co", "corp", "pvt", "limited", "technologies", "labs", "systems")

# Payload field → entity type. Structured events are the most reliable source of all.
FIELD_TYPES: dict[str, EntityType] = {
    "feature": EntityType.FEATURE,
    "feature_name": EntityType.FEATURE,
    "integration": EntityType.INTEGRATION,
    "provider": EntityType.INTEGRATION,
    "app": EntityType.INTEGRATION,
    "plan": EntityType.SUBSCRIPTION,
    "new_plan": EntityType.SUBSCRIPTION,
    "previous_plan": EntityType.SUBSCRIPTION,
    "product": EntityType.PRODUCT,
    "company": EntityType.COMPANY,
    "company_name": EntityType.COMPANY,
    "campaign": EntityType.CAMPAIGN,
    "order_id": EntityType.ORDER,
    "ticket_id": EntityType.SUPPORT_TICKET,
    "conversation_id": EntityType.CONVERSATION,
    "agent": EntityType.EMPLOYEE,
    "assignee": EntityType.EMPLOYEE,
}

_RELATIONSHIP_BY_TYPE: dict[EntityType, RelationshipType] = {
    EntityType.INTEGRATION: RelationshipType.USES,
    EntityType.FEATURE: RelationshipType.USES,
    EntityType.PRODUCT: RelationshipType.USES,
    EntityType.SUBSCRIPTION: RelationshipType.SUBSCRIBED_TO,
    EntityType.ORDER: RelationshipType.PURCHASED,
    EntityType.SUPPORT_TICKET: RelationshipType.CONTACTED,
    EntityType.CONVERSATION: RelationshipType.CONTACTED,
    EntityType.COMPANY: RelationshipType.WORKS_AT,
    EntityType.EMPLOYEE: RelationshipType.CONTACTED,
    EntityType.CAMPAIGN: RelationshipType.INTERESTED_IN,
}

_PROBLEM_MARKERS = ("fail", "error", "broken", "issue", "problem", "not work", "cannot", "crash", "timeout")


@dataclass(slots=True)
class EntityHit:
    type: EntityType
    name: str
    rule: str
    confidence: float = 0.8
    external_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.type.value, self.name.lower())

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "name": self.name,
            "rule": self.rule,
            "confidence": round(self.confidence, 3),
            "external_id": self.external_id,
            "attributes": self.attributes,
        }


def _dedupe(hits: Iterable[EntityHit]) -> list[EntityHit]:
    best: dict[tuple[str, str], EntityHit] = {}
    for hit in hits:
        existing = best.get(hit.key)
        if existing is None or hit.confidence > existing.confidence:
            best[hit.key] = hit
    return sorted(best.values(), key=lambda hit: (-hit.confidence, hit.name))


def from_gazetteer(text: str) -> list[EntityHit]:
    hits: list[EntityHit] = []
    lowered = f" {normalize(text).lower()} "
    lemmatised = f" {lemmatized_text(text)} "

    for needle, display in INTEGRATIONS.items():
        if f" {needle} " in lowered or f" {needle} " in lemmatised or f" {needle}'s " in lowered:
            hits.append(EntityHit(EntityType.INTEGRATION, display, rule="gazetteer:integration", confidence=0.95))

    # A plan name only counts when the sentence is actually about plans.
    plan_context = any(word in lemmatised for word in (" plan ", " subscription ", " upgrade ", " downgrade ", " tier ", " billing "))
    for needle, display in PLANS.items():
        if f" {needle} " in lowered and (plan_context or f" {needle} plan " in lowered):
            hits.append(EntityHit(EntityType.SUBSCRIPTION, display, rule="gazetteer:plan", confidence=0.85))

    return hits


def channels_mentioned(text: str) -> list[str]:
    """Contact channels named in the text, in the order they appear.

    Matched on tokens rather than raw text so trailing punctuation ("…prefers email.")
    cannot hide a channel and silently turn a conflict into a merge.
    """
    wanted, avoided = _stance(text)
    return _ordered([*wanted, *avoided])


# What turns a channel into one the customer does *not* want, within its clause: "WhatsApp
# instead of email", "call rather than email", "not by email", "don't email us", "stop
# calling", "no longer by phone". One token, or two read together.
_AVOID_ONE = frozenset({"not", "no", "never", "dont", "stop", "avoid", "without", "except", "nothing"})
_AVOID_TWO = frozenset({("instead", "of"), ("rather", "than"), ("no", "longer"), ("no", "more")})
_AVOID_REACH = 3
_CLAUSE = re.compile(r"[,;:.!?]+|\s(?:but|however|whereas)\s", re.IGNORECASE)


def _needles() -> list[tuple[tuple[str, ...], str]]:
    """Every channel needle as tokens, with the inflections a sentence uses: "emails",
    "emailing", "calls", "called". Not lemmas — "teams" must not become "team"."""
    found: list[tuple[tuple[str, ...], str]] = []
    for needle, display in CHANNELS.items():
        words = tuple(needle.split())
        found.append((words, display))
        if len(words) == 1:
            found.extend(((words[0] + suffix,), display) for suffix in ("s", "ing", "ed"))
        else:
            found.append((words[:-1] + (words[-1] + "s",), display))
    # Longest first, so "text message" is read before anything inside it.
    return sorted(found, key=lambda item: -len(item[0]))


_NEEDLES = _needles()


def channel_stance(text: str) -> tuple[list[str], list[str]]:
    """``(wanted, avoided)``: the contact channels the text asks for, and the ones it turns
    away from, each in the order they appear.

    "Please contact us on WhatsApp instead of email" wants WhatsApp and avoids email; a
    preference that only avoids ("don't email us") wants nothing — which is not the same as
    wanting whatever an older preference named.
    """
    wanted, avoided = _stance(text)
    return _ordered(wanted), _ordered(avoided)


def _stance(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    wanted: list[tuple[int, str]] = []
    avoided: list[tuple[int, str]] = []
    offset = 0
    for clause in _CLAUSE.split(text or ""):
        if not clause or not clause.strip():
            continue
        tokens = tokenize(clause)
        prefers = "prefer" in tokens or "prefers" in tokens or "preferred" in tokens
        taken: set[int] = set()
        for index in range(len(tokens)):
            for words, display in _NEEDLES:
                width = len(words)
                if index in taken or tuple(tokens[index : index + width]) != words:
                    continue
                taken.update(range(index, index + width))
                before = tokens[max(0, index - _AVOID_REACH) : index]
                pairs = set(zip(before, before[1:], strict=False))
                turned_away = bool(set(before) & _AVOID_ONE or pairs & _AVOID_TWO)
                # "we prefer WhatsApp over email"
                if prefers and before[-1:] == ["over"]:
                    turned_away = True
                (avoided if turned_away else wanted).append((offset + index, display))
                break
        offset += len(tokens) + 1
    return wanted, avoided


def _ordered(found: list[tuple[int, str]]) -> list[str]:
    seen: list[str] = []
    for _, display in sorted(found):
        if display not in seen:
            seen.append(display)
    return seen


def from_patterns(text: str) -> list[EntityHit]:
    hits: list[EntityHit] = []

    for match in TICKET_RE.finditer(text):
        hits.append(EntityHit(EntityType.SUPPORT_TICKET, f"Ticket {match.group(1)}",
                              rule="pattern:ticket", confidence=0.9, external_id=match.group(1)))
    for match in HASH_ID_RE.finditer(text):
        hits.append(EntityHit(EntityType.SUPPORT_TICKET, f"Ticket {match.group(1)}",
                              rule="pattern:hash_id", confidence=0.6, external_id=match.group(1)))
    for match in ORDER_RE.finditer(text):
        hits.append(EntityHit(EntityType.ORDER, f"Order {match.group(1)}",
                              rule="pattern:order", confidence=0.9, external_id=match.group(1)))
    for match in INVOICE_RE.finditer(text):
        hits.append(EntityHit(EntityType.OTHER, f"Invoice {match.group(1)}",
                              rule="pattern:invoice", confidence=0.85, external_id=match.group(1)))
    return hits


def measurements(text: str) -> dict[str, Any]:
    """Numbers worth keeping on a memory: amounts, percentages, dates and repetitions."""
    found: dict[str, Any] = {}
    money = MONEY_RE.findall(text)
    if money:
        found["amounts"] = [value.strip() for value in money]
    percent = PERCENT_RE.findall(text)
    if percent:
        found["percentages"] = percent
    dates = DATE_RE.findall(text)
    if dates:
        found["dates"] = dates
    versions = VERSION_RE.findall(text)
    if versions:
        found["versions"] = versions
    repetition = COUNT_RE.search(text)
    if repetition:
        raw = repetition.group(1).lower()
        found["occurrences"] = _WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else None)
    return {key: value for key, value in found.items() if value}


def _trim_span(candidate: str) -> str:
    """Stop a capitalised span at a sentence-ending word ("Acme Ltd. We" -> "Acme Ltd")."""
    words: list[str] = []
    for word in candidate.split():
        stripped = word.rstrip(".")
        words.append(stripped)
        if word.endswith(".") and len(stripped) > 2:
            break
    return " ".join(words).strip(" .,")


def from_proper_nouns(text: str) -> list[EntityHit]:
    hits: list[EntityHit] = []
    lowered = normalize(text).lower()
    known = {name.lower() for name in INTEGRATIONS.values()} | {name.lower() for name in PLANS.values()}

    for match in PROPER_NOUN_RE.finditer(text):
        candidate = _trim_span(match.group(1).strip())
        words = candidate.split()
        if not words:
            continue
        if any(word.lower() in _DOMAIN_STOPLIST for word in words):
            continue
        # "I've", "We'll": a contraction is never an entity.
        if any("'" in word and word.split("'")[0].lower() in PROPER_NOUN_STOPLIST for word in words):
            continue
        if all(word.lower().split("'")[0] in PROPER_NOUN_STOPLIST for word in words):
            continue
        if candidate.lower() in known or len(candidate) < 3:
            continue
        if all(word.lower() in PROPER_NOUN_STOPLIST for word in words):
            continue
        # Skip a capitalised first word of a sentence: too weak a signal on its own.
        if match.start() == 0 and len(words) == 1:
            continue
        if words[0].lower() in PROPER_NOUN_STOPLIST and len(words) == 1:
            continue

        preceding = lowered[max(0, match.start() - 24) : match.start()]
        tail = candidate.split()[-1].lower().strip(".")
        entity_type = EntityType.OTHER
        confidence = 0.5
        rule = "heuristic:proper_noun"

        if tail in _COMPANY_SUFFIXES or any(marker in preceding for marker in _COMPANY_MARKERS):
            entity_type, confidence, rule = EntityType.COMPANY, 0.75, "heuristic:company"
        elif any(marker in preceding for marker in _PERSON_MARKERS) or any(
            role in preceding for role in ROLES
        ):
            entity_type, confidence, rule = EntityType.EMPLOYEE, 0.65, "heuristic:person"

        if entity_type is EntityType.OTHER and len(words) == 1 and candidate.isalpha():
            # A single unknown capitalised word is usually a product or tool name.
            entity_type, confidence, rule = EntityType.PRODUCT, 0.45, "heuristic:product"

        hits.append(EntityHit(entity_type, candidate, rule=rule, confidence=confidence))
    return hits


def from_payload(data: dict[str, Any]) -> list[EntityHit]:
    hits: list[EntityHit] = []
    for field_name, entity_type in FIELD_TYPES.items():
        value = (data or {}).get(field_name)
        if value is None or isinstance(value, (dict, list, bool)):
            continue
        raw = str(value).strip()
        if not raw:
            continue
        name = INTEGRATIONS.get(raw.lower()) or PLANS.get(raw.lower()) or raw.replace("_", " ").strip()
        if entity_type in (EntityType.FEATURE, EntityType.PRODUCT, EntityType.CAMPAIGN):
            name = name.title()
        hits.append(
            EntityHit(
                entity_type,
                name,
                rule=f"payload:{field_name}",
                confidence=0.98,
                external_id=raw if entity_type in (EntityType.ORDER, EntityType.SUPPORT_TICKET) else None,
                attributes={"field": field_name},
            )
        )
    return hits


def extract(
    *,
    text: str = "",
    data: dict[str, Any] | None = None,
    include_proper_nouns: bool = True,
) -> list[EntityHit]:
    """All entities mentioned by an event, strongest evidence first."""
    hits: list[EntityHit] = []
    hits.extend(from_payload(data or {}))
    strong_names: set[str] = set()
    if text:
        gazetteer = from_gazetteer(text)
        patterns = from_patterns(text)
        hits.extend(gazetteer)
        hits.extend(patterns)
        strong_names = {
            word.lower()
            for hit in [*hits, *gazetteer, *patterns]
            for word in hit.name.split()
        }
        if include_proper_nouns:
            # A weak capitalised span that merely repeats a strong hit adds nothing.
            hits.extend(
                hit
                for hit in from_proper_nouns(text)
                if not {word.lower() for word in hit.name.split()} & strong_names
            )
    return _dedupe(hits)


def relationship_for(entity_type: EntityType, *, event_type: str = "", text: str = "") -> RelationshipType:
    """How the customer relates to an entity, given the surrounding context."""
    haystack = f"{event_type} {text}".lower()
    if entity_type in (EntityType.INTEGRATION, EntityType.FEATURE, EntityType.PRODUCT) and any(
        marker in haystack for marker in _PROBLEM_MARKERS
    ):
        return RelationshipType.HAS_PROBLEM
    return _RELATIONSHIP_BY_TYPE.get(entity_type, RelationshipType.RELATED_TO)


def mentions_customer_company(text: str) -> str | None:
    """"I work at Acme Ltd" → "Acme Ltd"."""
    tokens = tokenize(text)
    if not any(marker.replace(" ", "") in "".join(tokens) for marker in ("workat", "workfor")):
        return None
    for hit in from_proper_nouns(text):
        if hit.type is EntityType.COMPANY:
            return hit.name
    return None
