"""Why an agent did what it did: the decision trace of a run (§26 4.4).

A run records what the agent was given (§26 3.4). A decision trace adds what it was *not*
given, and why — because "why did my agent say that?" is as often answered by the memory it
never saw as by the one it did:

* **ranked below the cut** — retrieval found it, but others ranked higher;
* **capped by type** — the result already held enough memories of its kind;
* **dropped by the token budget** — the context was full; or its section was, or it said
  what a memory already in the context said;
* **superseded** — a newer memory replaced it (and was perhaps what the agent got);
* **expired** — it had lapsed;
* **withheld** — restricted, or outside the agent's profile.

Ranking reasons are recorded by retrieval as it ranks. The last three are *unseen*
memories: never candidates, so they are found here, by matching the question against the
customer's inactive and hidden memories with the same three signals retrieval uses to
widen recall — shared words, shared concepts and the memory types the question is about.

Everything here is pure; ids are recorded at run time and content is shaped for whoever
reads the trace later.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from common.time import ensure_utc
from nlp.concepts import concepts_for, dice
from nlp.tokenize import content_words

# How many unseen matches a run records, and how close a match must be.
UNSEEN_LIMIT = 5
MATCH_THRESHOLD = 0.3
# A memory of a type the question is about ("what problems…") matches on that alone.
TYPE_MATCH = 0.5

# Words in almost every question and every memory, which say nothing about the topic.
_GENERIC = frozenset(content_words("customer client user account they their them team company"))

REASONS = (
    "below_cut",
    "type_cap",
    "token_budget",
    "section_cap",
    "duplicate",
    "superseded",
    "expired",
    "withheld_restricted",
    "withheld_profile",
)


def _terms(query: str) -> tuple[set[str], list[str]]:
    return set(content_words(query)) - _GENERIC, concepts_for(query)


def _signals(
    query_words: set[str], query_concepts: Sequence[str], asked_types: Iterable[str], memory: Any
) -> tuple[float, float, float]:
    have = set(content_words(memory.content)) - _GENERIC
    lexical = len(query_words & have) / len(query_words) if query_words else 0.0
    conceptual = dice(query_concepts, memory.concepts or []) if query_concepts else 0.0
    typed = TYPE_MATCH if str(memory.type) in {str(kind) for kind in asked_types} else 0.0
    return lexical, conceptual, typed


def _blend(lexical: float, conceptual: float, typed: float) -> float:
    # The best signal, plus credit for the question's own words: two memories about the
    # same kind of thing ("a broken integration") are told apart by the one it names.
    return 0.6 * max(lexical, conceptual, typed) + 0.4 * lexical


def match_score(
    query_words: set[str], query_concepts: Sequence[str], asked_types: Iterable[str], memory: Any
) -> float:
    """How much a memory is about what was asked, 0..1, by the signals retrieval uses."""
    return _blend(*_signals(query_words, query_concepts, asked_types, memory))


def unseen(
    query: str,
    *,
    inactive: Sequence[Any],
    hidden: Sequence[Any],
    exclude: Iterable[str] = (),
    asked_types: Iterable[str] = (),
    cleared: bool = True,
    readable_types: frozenset[str] | None = None,
    limit: int = UNSEEN_LIMIT,
) -> list[dict[str, Any]]:
    """Memories that match the question but that the agent could not have been given,
    best match first, as ids and reasons (no content: that is shaped on read)."""
    words, concepts = _terms(query)
    asked = list(asked_types)
    skip = set(exclude)
    found: list[tuple[tuple[float, float, float], dict[str, Any]]] = []
    for memory in [*hidden, *inactive]:
        if memory.id in skip:
            continue
        lexical, conceptual, typed = _signals(words, concepts, asked, memory)
        score = _blend(lexical, conceptual, typed)
        if score < MATCH_THRESHOLD:
            continue
        skip.add(memory.id)
        entry: dict[str, Any] = {"memory_id": memory.id, "type": str(memory.type), "match": round(score, 3)}
        status = str(memory.status)
        if status == "superseded":
            entry.update(reason="superseded", superseded_by=memory.superseded_by)
        elif status == "expired":
            entry.update(reason="expired", expired_at=_iso(memory.expires_at))
        elif not cleared and str(memory.sensitivity) == "restricted":
            entry.update(reason="withheld_restricted")
        elif readable_types is not None and str(memory.type) not in readable_types:
            entry.update(reason="withheld_profile")
        else:
            continue
        found.append(((score, lexical, conceptual), entry))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in found[:limit]]


def _iso(moment: datetime | None) -> str | None:
    return ensure_utc(moment).isoformat() if moment else None


# ---------------------------------------------------------------- in words


def _strategies(item: dict[str, Any]) -> str:
    names = [name for name in item.get("strategies") or [] if name != "fallback"]
    return ", ".join(names) if names else "the customer's most important memories"


def why_given(item: dict[str, Any], *, kind: str) -> tuple[str, str]:
    """The verdict on a memory the agent was given, and the sentence behind it.

    ``cited`` — the answer rests on it; ``given`` — handed to the agent in a context;
    ``not_cited`` — retrieved for an answer that drew on other evidence.
    """
    rank, score = item.get("rank"), float(item.get("score") or 0.0)
    found = f"found by {_strategies(item)}, score {score:.2f}"
    if kind == "context":
        return "given", f"#{rank} in the context — {found}."
    if item.get("cited"):
        return "cited", f"#{rank}, cited by the answer — {found}."
    return "not_cited", f"#{rank}, retrieved but not cited — the answer drew on other evidence ({found})."


def why_ignored(
    entry: dict[str, Any],
    *,
    limit: int | None = None,
    per_type: int | None = None,
    token_budget: int | None = None,
    profile: str | None = None,
    replacement: str | None = None,
    replacement_rank: int | None = None,
    superseded_at: datetime | None = None,
    merged: bool = False,
) -> str:
    """Why a memory the question matched did not reach the agent, in one sentence."""
    reason = entry.get("reason")
    score = entry.get("score")
    kind = str(entry.get("type") or "memory")
    if reason == "below_cut":
        cut = f"only the top {limit} were returned" if limit else "others ranked higher"
        return f"Ranked #{entry.get('position')} (score {float(score or 0):.2f}); {cut}."
    if reason == "type_cap":
        cap = f"{per_type} {kind} memories" if per_type else f"enough {kind} memories"
        return f"Ranked #{entry.get('position')} (score {float(score or 0):.2f}), but {cap} were already included."
    if reason == "token_budget":
        budget = f" of {token_budget} tokens" if token_budget else ""
        return f"Retrieved, but dropped to fit the token budget{budget}."
    if reason == "section_cap":
        return f"Its section of the context was full — a briefing holds only so many {kind} memories."
    if reason == "duplicate":
        return "Said what a memory already in the context says, so it was left out."
    if reason == "superseded":
        when = f" on {superseded_at:%d %b %Y}" if superseded_at else ""
        # The nightly sweep folds a duplicate into the memory that says the same thing; a
        # conflict or a correction replaces one statement with a newer one.
        text = f"Merged{when} into a memory that says the same" if merged else f"Superseded{when} by a newer memory"
        if replacement:
            text += f": “{_clip(replacement)}”"
        if replacement_rank:
            text += f" — which the agent was given (#{replacement_rank})"
        return text if text.endswith("”") else text + "."
    if reason == "expired":
        at = entry.get("expired_at")
        when = f" on {ensure_utc(datetime.fromisoformat(at)):%d %b %Y}" if isinstance(at, str) else ""
        return f"Expired{when}: {kind} memories lapse when nothing renews them."
    if reason == "withheld_restricted":
        return "Restricted by the project's policy, and the agent's key has no clearance."
    if reason == "withheld_profile":
        who = f"The {profile} profile" if profile else "The agent's profile"
        return f"{who} does not read {kind} memories."
    return "Not given to the agent."


def narrative(
    *,
    kind: str,
    agent: str | None,
    query: str,
    given: Sequence[dict[str, Any]],
    ignored: Sequence[dict[str, Any]],
    decision: dict[str, Any],
    checks: Sequence[Any] = (),
) -> list[str]:
    """The trace as sentences, each derived from a field shown beside it."""
    lines: list[str] = []
    who = agent or "The caller"
    lines.append(f"{who} {'asked' if kind == 'query' else 'requested a briefing for'}: “{query}”.")
    verdicts: dict[str, int] = {}
    for item in given:
        verdicts[item["verdict"]] = verdicts.get(item["verdict"], 0) + 1
    if kind == "query":
        cited = verdicts.get("cited", 0)
        lines.append(
            f"{len(given)} memor{'y was' if len(given) == 1 else 'ies were'} retrieved; the answer cited {cited}."
            if given
            else "Nothing was retrieved."
        )
    else:
        lines.append(f"The agent was handed {len(given)} memor{'y' if len(given) == 1 else 'ies'}.")
    by_reason: dict[str, int] = {}
    for entry in ignored:
        by_reason[entry["reason"]] = by_reason.get(entry["reason"], 0) + 1
    words = {
        "below_cut": "ranked below the cut",
        "type_cap": "capped by type",
        "token_budget": "dropped by the token budget",
        "section_cap": "left out of a full section",
        "duplicate": "near-duplicates of what was given",
        "superseded": "superseded by newer memories",
        "expired": "expired",
        "withheld_restricted": "restricted",
        "withheld_profile": "outside the agent's profile",
    }
    if by_reason:
        parts = [f"{count} {words.get(reason, reason)}" for reason, count in by_reason.items()]
        lines.append("Considered but not given: " + ", ".join(parts) + ".")
    superseded = [entry for entry in ignored if entry["reason"] == "superseded" and entry.get("replacement_rank")]
    for entry in superseded[:2]:
        lines.append(f"The agent saw the newer version (#{entry['replacement_rank']}) of a memory that was superseded.")
    if decision.get("kind") == "answer":
        confidence = decision.get("confidence")
        strategy = decision.get("strategy")
        how = f" by {str(strategy).replace('_', ' ')}" if strategy else ""
        conf = f" with confidence {float(confidence):.2f}" if confidence is not None else ""
        lines.append(f"The answer was composed{how}{conf}.")
    elif decision.get("kind") == "context" and decision.get("truncated"):
        lines.append(f"The context was cut to {decision.get('token_count')} tokens.")
    for check in list(checks)[:3]:
        lines.append(f"Guardrail check: {check.action} → {check.decision}. {check.summary}")
    return lines


def _clip(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
