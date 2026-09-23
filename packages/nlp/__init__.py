"""Deterministic language engine.

This package is what the product uses instead of an LLM. Every function here is
rule-based and pure: the same input always produces the same output, on any machine,
with no API key, no network call, no per-request cost and no data leaving the deployment.

    events → sentences → classified statements → third-person memories
    question → intent → retrieval hints → composed answer with citations

The trade-off is explicit: the engine understands the vocabulary in ``nlp.lexicon`` and
the structures in ``nlp.templates``, not arbitrary world knowledge. In exchange, every
memory and every answer can be traced to the exact rule and cue that produced it.
"""

from nlp.answer import Answer, EventView, MemoryView, compose
from nlp.classify import Classification, classify
from nlp.embeddings import MODEL_NAME, LocalEmbedder, cosine, embed_text
from nlp.entities import EntityHit, channels_mentioned, measurements, relationship_for
from nlp.entities import extract as extract_entities
from nlp.extract import ExtractionOutput, MemoryCandidate, extract
from nlp.question import QuestionAnalysis, QuestionIntent
from nlp.question import analyze as analyze_question
from nlp.rewrite import to_third_person
from nlp.sentiment import Sentiment
from nlp.sentiment import analyze as analyze_sentiment
from nlp.summarize import deduplicate, key_phrases, mmr_select, summarize
from nlp.templates import build as build_template
from nlp.tokenize import lemmatize, split_sentences, tokenize

__all__ = [
    "MODEL_NAME",
    "Answer",
    "Classification",
    "EntityHit",
    "EventView",
    "ExtractionOutput",
    "LocalEmbedder",
    "MemoryCandidate",
    "MemoryView",
    "QuestionAnalysis",
    "QuestionIntent",
    "Sentiment",
    "analyze_question",
    "analyze_sentiment",
    "build_template",
    "channels_mentioned",
    "classify",
    "compose",
    "cosine",
    "deduplicate",
    "embed_text",
    "extract",
    "extract_entities",
    "key_phrases",
    "lemmatize",
    "measurements",
    "mmr_select",
    "relationship_for",
    "split_sentences",
    "summarize",
    "to_third_person",
    "tokenize",
]
