"""The decision trace: what an agent was not given, and why (§26 4.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from memory_engine import decision_trace
from memory_engine.retrieval.retriever import _passed_over
from memory_engine.schemas import ScoredMemory
from nlp.concepts import concepts_for


def memory(ident, kind, content, *, status="active", sensitivity="normal", superseded_by=None, expires_at=None):
    return SimpleNamespace(
        id=ident,
        type=kind,
        content=content,
        concepts=concepts_for(content),
        status=status,
        sensitivity=sensitivity,
        superseded_by=superseded_by,
        expires_at=expires_at,
        importance=0.5,
        confidence=0.8,
    )


def scored(ident, kind, score):
    item = ScoredMemory(memory=memory(ident, kind, f"memory {ident}"))
    item.score = score
    return item


# ------------------------------------------------------------ ranking reasons


def test_retrieval_says_which_candidates_it_passed_over_and_why():
    everything = [scored("a", "problem", 0.9), scored("b", "problem", 0.8), scored("c", "problem", 0.7), scored("d", "fact", 0.6), scored("e", "fact", 0.5)]
    diverse = [item for item in everything if item.memory.id != "c"]  # a third problem is over the cap
    returned = diverse[:3]
    passed = _passed_over(everything, diverse, returned)
    assert [(item.item.memory.id, item.reason, item.position) for item in passed] == [
        ("c", "type_cap", 3),
        ("e", "below_cut", 5),
    ]
    assert passed[0].explain()["type"] == "problem"


# ------------------------------------------------------------- unseen matches


def test_unseen_matches_carry_their_reason_best_first():
    old = memory("p0", "problem", "The Shopify sync fails during checkout.", status="superseded", superseded_by="p1")
    lapsed = memory("i0", "intent", "Wants a Shopify sync demo next quarter.", status="expired", expires_at=datetime(2026, 8, 1, tzinfo=UTC))
    secret = memory("s0", "problem", "The Stripe payout of their salary failed.", sensitivity="restricted")
    unrelated = memory("x0", "fact", "Has 40 stores in Gujarat.", status="superseded")
    found = decision_trace.unseen(
        "Is the Shopify sync still failing?",
        inactive=[old, lapsed, unrelated],
        hidden=[secret],
        asked_types=["problem"],
        cleared=False,
    )
    reasons = {entry["memory_id"]: entry["reason"] for entry in found}
    assert reasons == {"p0": "superseded", "i0": "expired", "s0": "withheld_restricted"}
    assert found[0]["memory_id"] == "p0"  # the closest match first
    assert found[0]["superseded_by"] == "p1"
    assert "x0" not in reasons  # superseded, but not about what was asked


def test_a_profile_hides_by_type_and_given_memories_are_not_unseen():
    feedback = memory("f0", "feedback", "The Shopify sync is frustrating.")
    found = decision_trace.unseen(
        "How do they feel about the Shopify sync?",
        inactive=[],
        hidden=[feedback],
        readable_types=frozenset({"problem"}),
    )
    assert [entry["reason"] for entry in found] == ["withheld_profile"]
    assert decision_trace.unseen("Shopify sync", inactive=[], hidden=[feedback], exclude={"f0"}, readable_types=frozenset()) == []


def test_generic_words_do_not_make_a_match():
    about_customer = memory("m0", "fact", "The customer has a team account.", status="superseded")
    assert decision_trace.unseen("What does the customer want?", inactive=[about_customer], hidden=[]) == []


# ------------------------------------------------------------------ in words


def test_every_verdict_and_reason_reads_as_a_sentence():
    assert decision_trace.why_given({"rank": 1, "score": 0.82, "cited": True, "strategies": ["keyword", "semantic"]}, kind="query") == (
        "cited",
        "#1, cited by the answer — found by keyword, semantic, score 0.82.",
    )
    assert decision_trace.why_given({"rank": 4, "score": 0.3, "strategies": ["type"]}, kind="query")[0] == "not_cited"
    assert decision_trace.why_given({"rank": 2, "score": 0.5, "strategies": ["fallback"]}, kind="context")[1] == (
        "#2 in the context — found by the customer's most important memories, score 0.50."
    )
    assert decision_trace.why_ignored({"reason": "below_cut", "position": 12, "score": 0.21}, limit=10) == (
        "Ranked #12 (score 0.21); only the top 10 were returned."
    )
    assert decision_trace.why_ignored({"reason": "type_cap", "position": 6, "score": 0.4, "type": "problem"}, per_type=5) == (
        "Ranked #6 (score 0.40), but 5 problem memories were already included."
    )
    assert decision_trace.why_ignored({"reason": "token_budget"}, token_budget=800) == "Retrieved, but dropped to fit the token budget of 800 tokens."
    assert decision_trace.why_ignored(
        {"reason": "superseded"},
        replacement="The Shopify sync works now.",
        replacement_rank=1,
        superseded_at=datetime(2026, 9, 12, tzinfo=UTC),
    ) == "Superseded on 12 Sep 2026 by a newer memory: “The Shopify sync works now.” — which the agent was given (#1)."
    assert decision_trace.why_ignored({"reason": "expired", "type": "intent", "expired_at": "2026-08-01T00:00:00+00:00"}) == (
        "Expired on 01 Aug 2026: intent memories lapse when nothing renews them."
    )
    assert decision_trace.why_ignored({"reason": "withheld_profile", "type": "feedback"}, profile="sales-agent") == (
        "The sales-agent profile does not read feedback memories."
    )
    assert "no clearance" in decision_trace.why_ignored({"reason": "withheld_restricted"})


def test_the_narrative_counts_what_was_used_and_what_was_not():
    lines = decision_trace.narrative(
        kind="query",
        agent="support-bot",
        query="Is the Shopify sync still failing?",
        given=[{"verdict": "cited"}, {"verdict": "not_cited"}],
        ignored=[{"reason": "superseded", "replacement_rank": 1}, {"reason": "below_cut"}, {"reason": "below_cut"}],
        decision={"kind": "answer", "strategy": "status_check", "confidence": 0.82},
    )
    assert lines == [
        "support-bot asked: “Is the Shopify sync still failing?”.",
        "2 memories were retrieved; the answer cited 1.",
        "Considered but not given: 1 superseded by newer memories, 2 ranked below the cut.",
        "The agent saw the newer version (#1) of a memory that was superseded.",
        "The answer was composed by status check with confidence 0.82.",
    ]


def test_keyword_terms_skip_words_every_memory_has():
    from database.repositories.memories import _SEARCH_SYNTAX, _UNSELECTIVE

    assert "customer" in _UNSELECTIVE
    assert _SEARCH_SYNTAX.search('"shopify sync"') and _SEARCH_SYNTAX.search("sync -billing")
    assert not _SEARCH_SYNTAX.search("Are the webhook retries still failing?")
    assert not _SEARCH_SYNTAX.search("The error-prone sync")  # a hyphen inside a word is not syntax


def test_a_merge_is_not_called_a_replacement():
    text = decision_trace.why_ignored(
        {"reason": "superseded"}, replacement="Invoices fail to send.", merged=True, superseded_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    assert text == "Merged on 01 Sep 2026 into a memory that says the same: “Invoices fail to send.”"
