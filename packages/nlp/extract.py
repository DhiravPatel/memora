"""Event → candidate memories, deterministically.

Two sources feed extraction and both are rule-driven:

* **Prose** — free-text fields are split into sentences and clauses, filtered, classified,
  rewritten into third person and scored.
* **Structure** — the event type and payload produce a templated statement.

Everything a candidate asserts is traceable to the sentence or field it came from, and the
cues that classified it are attached to the memory's metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from common.enums import EntityType, MemoryType, RelationshipType
from common.text import content_hash
from nlp import entities as entity_rules
from nlp.classify import Classification, classify
from nlp.entities import EntityHit
from nlp.intents import intent_kinds
from nlp.lexicon import FILLER_SENTENCES
from nlp.rewrite import to_third_person
from nlp.sentiment import Sentiment, analyze
from nlp.templates import build as build_template
from nlp.tokenize import (
    correct_spelling,
    is_question,
    normalize,
    split_clauses,
    split_sentences,
    word_count,
)

# Payload fields that contain something a human wrote, in priority order.
TEXT_FIELDS: tuple[str, ...] = (
    "message", "text", "body", "comment", "feedback", "reason", "note", "notes", "content",
    "description", "subject", "title", "answer", "response", "summary", "transcript",
)

MIN_WORDS = 3
MAX_CONTENT_LENGTH = 600
MAX_MEMORIES_PER_EVENT = 6

# A question is only worth remembering when it also reports something.
_QUESTION_KEEP_TYPES = {MemoryType.PROBLEM, MemoryType.GOAL, MemoryType.INTENT}

# Pronouns that need a referent before a clause can stand on its own as a memory.
_LEADING_PRONOUN = re.compile(r"^(it|this|that|they|these|those|the same)\b[\s,]*", re.I)

# Entity types that can be the topic of a sentence.
_TOPIC_TYPES = (
    EntityType.INTEGRATION,
    EntityType.FEATURE,
    EntityType.PRODUCT,
    EntityType.SUBSCRIPTION,
    EntityType.ORDER,
)

_TOPIC_PHRASES: dict[EntityType, str] = {
    EntityType.INTEGRATION: "The {name} integration",
    EntityType.FEATURE: "The {name} feature",
    EntityType.SUBSCRIPTION: "The {name} plan",
    EntityType.ORDER: "{name}",
    EntityType.PRODUCT: "{name}",
}

_TYPE_BASE_IMPORTANCE: dict[MemoryType, float] = {
    MemoryType.PROBLEM: 0.78,
    MemoryType.SUBSCRIPTION: 0.8,
    MemoryType.INTENT: 0.78,
    MemoryType.FEEDBACK: 0.65,
    MemoryType.GOAL: 0.66,
    MemoryType.PREFERENCE: 0.62,
    MemoryType.RELATIONSHIP: 0.55,
    MemoryType.FACT: 0.5,
    MemoryType.SUMMARY: 0.5,
    MemoryType.BEHAVIOR: 0.34,
}


@dataclass(slots=True)
class MemoryCandidate:
    type: MemoryType
    content: str
    importance: float
    confidence: float
    source: str  # "text" | "template"
    rule: str
    entities: list[EntityHit] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return content_hash(self.content)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "content": self.content,
            "importance": round(self.importance, 3),
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "rule": self.rule,
            "entities": [hit.as_dict() for hit in self.entities],
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class ExtractionOutput:
    memories: list[MemoryCandidate] = field(default_factory=list)
    entities: list[EntityHit] = field(default_factory=list)
    relationships: list[tuple[str, RelationshipType, str, float]] = field(default_factory=list)
    sentiment: Sentiment | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "memories": [memory.as_dict() for memory in self.memories],
            "entities": [hit.as_dict() for hit in self.entities],
            "relationships": [
                {"source": source, "type": relation.value, "target": target, "confidence": confidence}
                for source, relation, target, confidence in self.relationships
            ],
            "sentiment": self.sentiment.as_dict() if self.sentiment else None,
            "stats": self.stats,
        }


# Fields whose text is the customer's stated reason rather than a direct message; a
# statement lifted from them needs attribution to stand on its own.
ATTRIBUTED_FIELDS: frozenset[str] = frozenset(
    {"reason", "feedback", "comment", "note", "notes", "answer", "response", "summary"}
)
ATTRIBUTION_PREFIX = "The customer reported that"


def collect_segments(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Human-written text from a payload as ``(field, text)``, in priority order."""
    seen: set[str] = set()
    segments: list[tuple[str, str]] = []
    for field_name in TEXT_FIELDS:
        value = (data or {}).get(field_name)
        if not isinstance(value, str):
            continue
        cleaned = normalize(value)
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        segments.append((field_name, cleaned))
    return segments


def collect_text(data: dict[str, Any]) -> str:
    """Human-written text from a payload, in priority order, de-duplicated."""
    return "\n".join(text for _, text in collect_segments(data))


def attribute(content: str) -> str:
    """"Integration never worked" → "The customer reported that integration never worked"."""
    stripped = content.strip()
    if not stripped:
        return stripped
    lowered = stripped.lower()
    if lowered.startswith(("the customer", "customer said", "please contact")):
        return stripped
    body = stripped[0].lower() + stripped[1:]
    return f"{ATTRIBUTION_PREFIX} {body}"


def is_filler(sentence: str) -> bool:
    stripped = normalize(sentence).strip(" .!?,").lower()
    if not stripped:
        return True
    if stripped in FILLER_SENTENCES:
        return True
    return word_count(stripped) < MIN_WORDS


def _importance_for(classification: Classification, sentiment: Sentiment) -> float:
    base = _TYPE_BASE_IMPORTANCE.get(classification.type, 0.5)
    score = base
    score += 0.18 * sentiment.negativity
    score += 0.15 * sentiment.urgency
    score += 0.25 * sentiment.churn_risk
    if classification.resolved:
        score -= 0.2
    if classification.type is MemoryType.PROBLEM and sentiment.urgency >= 0.6:
        score += 0.05
    return round(max(0.05, min(1.0, score)), 4)


def _topic_phrase(hit: EntityHit) -> str:
    template = _TOPIC_PHRASES.get(hit.type, "{name}")
    return template.format(name=hit.name)


def resolve_leading_pronoun(clause: str, topic: EntityHit | None) -> str:
    """"It still doesn't work" → "The Shopify integration still doesn't work".

    A memory has to stand on its own: the sentence that produced it will not be next to it
    when an agent reads it back six weeks later.
    """
    if topic is None:
        return clause
    match = _LEADING_PRONOUN.match(clause)
    if not match:
        return clause
    remainder = clause[match.end() :].strip()
    if not remainder:
        return clause
    return f"{_topic_phrase(topic)} {remainder}"


# A threat to leave or shrink that hangs on a problem — "we will cancel if the export keeps
# failing" — says two things: what the customer intends (the whole sentence) and what is
# wrong (the condition). Kept as one problem memory, the threat is lost the first time a
# later report rewrites the problem; kept as one intent, the problem never meets its own
# earlier reports.
_THREAT_KINDS = frozenset({"cancellation", "downgrade"})
_CONDITION_AFTER = re.compile(r"^(?P<main>.+?),?\s+(?:if|unless|until)\s+(?P<condition>.+)$", re.IGNORECASE)
_CONDITION_FIRST = re.compile(r"^\s*(?:if|unless|until)\s+(?P<condition>[^,]+),\s*(?P<main>.+)$", re.IGNORECASE)
# A condition addressed to the vendor ("unless you fix it") is a demand, not a problem.
_DEMAND = re.compile(r"^\s*(?:you|they)\b", re.IGNORECASE)
# A threat has the customer as its subject: "we will cancel", not "please cancel the invoice".
_CUSTOMER_SUBJECT = re.compile(r"\b(?:we|i|we'll|i'll|our (?:team|company|business))\b", re.IGNORECASE)


def conditional_threat(clause: str) -> tuple[list[str], str | None] | None:
    """``(threat kinds, problem condition)`` when ``clause`` threatens to leave or shrink on
    a condition; the condition is ``None`` when it is not a problem statement."""
    match = _CONDITION_FIRST.match(clause) or _CONDITION_AFTER.match(clause)
    if match is None:
        return None
    main = match.group("main")
    kinds = [kind for kind in intent_kinds(main) if kind in _THREAT_KINDS]
    if not kinds or not _CUSTOMER_SUBJECT.search(main):
        return None
    condition = match.group("condition").strip().rstrip(".!")
    if _DEMAND.match(condition) or word_count(condition) < MIN_WORDS:
        return kinds, None
    return kinds, condition


def extract_statements(
    text: str,
    *,
    event_type: str = "",
    event_entities: list[EntityHit] | None = None,
    attribution: bool = False,
) -> list[MemoryCandidate]:
    """Turn prose into memory candidates, one per meaningful clause."""
    candidates: list[MemoryCandidate] = []
    seen_hashes: set[str] = set()

    # The topic carries across clauses and sentences so pronouns can be resolved.
    topic: EntityHit | None = next(
        (hit for hit in (event_entities or []) if hit.type in _TOPIC_TYPES), None
    )

    for sentence in split_sentences(correct_spelling(text)):
        if is_filler(sentence):
            continue
        for raw_clause in split_clauses(sentence):
            if is_filler(raw_clause):
                continue

            clause_hits = entity_rules.extract(text=raw_clause)
            clause_topic = next((hit for hit in clause_hits if hit.type in _TOPIC_TYPES), None)
            clause = resolve_leading_pronoun(raw_clause, topic if clause_topic is None else clause_topic)
            if clause_topic is not None:
                topic = clause_topic

            classification = classify(clause, event_type=event_type)
            sentiment = classification.sentiment or analyze(clause)

            if is_question(clause) and classification.type not in _QUESTION_KEEP_TYPES:
                continue

            threat = conditional_threat(clause)
            rule_suffix = ""
            if threat is not None:
                kinds, condition = threat
                if condition is not None:
                    facet = classify(condition, event_type=event_type)
                    if facet.type is MemoryType.PROBLEM:
                        # The problem, on its own terms, so it meets its earlier reports.
                        problem = _candidate(
                            condition,
                            facet,
                            facet.sentiment or analyze(condition),
                            hits=clause_hits or ([topic] if topic is not None else []),
                            event_entities=event_entities,
                            source_sentence=raw_clause,
                            attribution=attribution,
                            rule="text:problem:condition",
                        )
                        if problem is not None and problem.hash not in seen_hashes:
                            seen_hashes.add(problem.hash)
                            candidates.append(problem)
                # The whole sentence is what they intend.
                classification = Classification(
                    type=MemoryType.INTENT,
                    confidence=max(classification.confidence, 0.75),
                    scores=classification.scores,
                    cues=[*kinds, *classification.cues],
                    negated_cues=classification.negated_cues,
                    resolved=False,
                    sentiment=sentiment,
                    margin=classification.margin,
                )
                rule_suffix = ":conditional_threat"

            rewritten = to_third_person(clause)
            if not rewritten.text:
                continue
            content = rewritten.text
            if attribution and not rewritten.changed and not rewritten.quoted:
                content = attribute(content)
            content = content[:MAX_CONTENT_LENGTH]
            digest = content_hash(content)
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            hits = clause_hits or ([topic] if topic is not None else [])
            confidence = classification.confidence * (0.9 if rewritten.quoted else 1.0)

            candidates.append(
                MemoryCandidate(
                    type=classification.type,
                    content=content,
                    importance=_importance_for(classification, sentiment),
                    confidence=round(min(0.99, confidence), 4),
                    source="text",
                    rule=f"text:{classification.type.value}{rule_suffix}",
                    entities=hits or list(event_entities or []),
                    attributes={
                        "cues": classification.cues,
                        "negated_cues": classification.negated_cues,
                        "resolved": classification.resolved,
                        "quoted": rewritten.quoted,
                        "sentiment": sentiment.as_dict(),
                        "measurements": entity_rules.measurements(clause),
                        "source_sentence": raw_clause[:MAX_CONTENT_LENGTH],
                        "channels": entity_rules.channels_mentioned(clause),
                    },
                )
            )
    return candidates


def _candidate(
    clause: str,
    classification: Classification,
    sentiment: Sentiment,
    *,
    hits: list[EntityHit],
    event_entities: list[EntityHit] | None,
    source_sentence: str,
    attribution: bool,
    rule: str,
) -> MemoryCandidate | None:
    """One clause as a memory candidate — the path every prose statement takes."""
    rewritten = to_third_person(clause)
    if not rewritten.text:
        return None
    content = rewritten.text
    if attribution and not rewritten.changed and not rewritten.quoted:
        content = attribute(content)
    content = content[:MAX_CONTENT_LENGTH]
    confidence = classification.confidence * (0.9 if rewritten.quoted else 1.0)
    return MemoryCandidate(
        type=classification.type,
        content=content,
        importance=_importance_for(classification, sentiment),
        confidence=round(min(0.99, confidence), 4),
        source="text",
        rule=rule,
        entities=hits or list(event_entities or []),
        attributes={
            "cues": classification.cues,
            "negated_cues": classification.negated_cues,
            "resolved": classification.resolved,
            "quoted": rewritten.quoted,
            "sentiment": sentiment.as_dict(),
            "measurements": entity_rules.measurements(clause),
            "source_sentence": source_sentence[:MAX_CONTENT_LENGTH],
            "channels": entity_rules.channels_mentioned(clause),
        },
    )


def extract(
    *,
    event_type: str,
    data: dict[str, Any] | None = None,
    text: str | None = None,
    max_memories: int = MAX_MEMORIES_PER_EVENT,
    customer_label: str = "The customer",
) -> ExtractionOutput:
    """Everything this event tells us about the customer."""
    payload = data or {}
    prose = text if text is not None else collect_text(payload)
    event_entities = entity_rules.extract(text=prose, data=payload)

    memories: list[MemoryCandidate] = []
    templated = build_template(event_type, payload)
    if templated is not None:
        memories.append(
            MemoryCandidate(
                type=templated.type,
                content=templated.content,
                importance=templated.importance,
                confidence=templated.confidence,
                source="template",
                rule=templated.rule,
                entities=[hit for hit in event_entities if hit.rule.startswith("payload:")],
                attributes=templated.attributes,
            )
        )

    for field_name, segment in collect_segments(payload) if text is None else [("text", prose)]:
        memories.extend(
            extract_statements(
                segment,
                event_type=event_type,
                event_entities=event_entities,
                attribution=field_name in ATTRIBUTED_FIELDS,
            )
        )

    # Drop prose candidates that merely restate the template.
    deduped: list[MemoryCandidate] = []
    seen: set[str] = set()
    for candidate in memories:
        if candidate.hash in seen:
            continue
        seen.add(candidate.hash)
        deduped.append(candidate)

    deduped.sort(key=lambda candidate: (-candidate.importance, -candidate.confidence))
    selected = deduped[:max_memories]

    relationships: list[tuple[str, RelationshipType, str, float]] = []
    for hit in event_entities:
        if hit.type is EntityType.CUSTOMER:
            continue
        relationships.append(
            (
                customer_label,
                entity_rules.relationship_for(hit.type, event_type=event_type, text=prose),
                hit.name,
                round(min(0.95, hit.confidence), 3),
            )
        )

    overall_sentiment = analyze(prose) if prose else None
    return ExtractionOutput(
        memories=selected,
        entities=event_entities,
        relationships=relationships,
        sentiment=overall_sentiment,
        stats={
            "sentences": len(split_sentences(prose)) if prose else 0,
            "candidates": len(deduped),
            "selected": len(selected),
            "has_text": bool(prose),
            "templated": templated is not None,
        },
    )
