"""Health scoring and causal linking: the analytics derived from memory."""

from __future__ import annotations

from datetime import timedelta

from common.enums import MemoryType
from common.time import utcnow
from memory_engine.analytics import band_for, compute
from memory_engine.linking import LinkType, chain_for, infer, is_outcome
from memory_engine.linking.causal import MAX_LINKS_PER_MEMORY, RELATED_LOOKBACK
from nlp.answer import MemoryView

NOW = utcnow()


def memory(
    identifier: str,
    memory_type: MemoryType,
    content: str,
    *,
    days: int = 2,
    importance: float = 0.8,
    evidence: int = 1,
    entities: tuple[str, ...] = (),
    attributes: dict | None = None,
) -> MemoryView:
    return MemoryView(
        id=identifier,
        type=memory_type,
        content=content,
        importance=importance,
        confidence=0.9,
        last_seen_at=NOW - timedelta(days=days),
        first_seen_at=NOW - timedelta(days=days + 3),
        evidence_count=evidence,
        entities=list(entities),
        attributes=attributes or {},
    )


# ------------------------------------------------------------------- health


def test_unhappy_customer_scores_low_and_explains_why():
    score = compute(
        memories=[
            memory("m1", MemoryType.PROBLEM, "The Shopify integration still does not work.",
                   evidence=3, attributes={"sentiment": {"urgency": 0.8}}),
            memory("m2", MemoryType.SUBSCRIPTION,
                   "The customer downgraded from the Pro plan to the Starter plan."),
        ],
        event_count=12,
        last_event_at=NOW - timedelta(days=2),
    )
    assert score.band in ("at_risk", "critical")
    assert score.churn_risk > 0.5
    assert score.is_at_risk
    keys = {factor.key for factor in score.factors}
    assert {"open_problems", "downgrade"} <= keys
    assert "pulled down by" in score.as_dict()["explanation"]


def test_happy_customer_scores_high():
    score = compute(
        memories=[
            memory("m3", MemoryType.SUBSCRIPTION,
                   "The customer upgraded from the Starter plan to the Pro plan."),
            memory("m4", MemoryType.FEEDBACK, "The customer loves the reporting.", importance=0.6),
            memory("m5", MemoryType.GOAL, "The customer's goal is to migrate 2M records."),
        ],
        event_count=40,
        last_event_at=NOW - timedelta(days=1),
        distinct_features=4,
    )
    assert score.band == "healthy"
    assert score.churn_risk < 0.2


def test_silence_costs_points():
    active = compute(memories=[], event_count=5, last_event_at=NOW - timedelta(days=3))
    quiet = compute(memories=[], event_count=5, last_event_at=NOW - timedelta(days=120))
    assert quiet.score < active.score
    assert any(factor.key == "silence" for factor in quiet.factors)


def test_resolved_problems_are_less_damaging_than_open_ones():
    open_problem = compute(
        memories=[memory("m6", MemoryType.PROBLEM, "Sync is broken.")], event_count=3
    )
    resolved = compute(
        memories=[
            memory("m7", MemoryType.PROBLEM, "Sync is broken.", attributes={"resolved": True})
        ],
        event_count=3,
    )
    assert resolved.score > open_problem.score


def test_score_is_bounded_and_banded():
    empty = compute(memories=[], event_count=0)
    assert 0 <= empty.score <= 100
    assert band_for(95) == "healthy"
    assert band_for(65) == "watch"
    assert band_for(40) == "at_risk"
    assert band_for(10) == "critical"


# ------------------------------------------------------------------ linking


def story() -> list[MemoryView]:
    return [
        memory("p1", MemoryType.PROBLEM, "The Shopify integration failed.", days=9,
               entities=("Shopify",), evidence=2),
        memory("p2", MemoryType.PROBLEM, "The Shopify sync still does not work.", days=6,
               entities=("Shopify",)),
        memory("s1", MemoryType.SUBSCRIPTION,
               "The customer downgraded from the Pro plan to the Starter plan.", days=1,
               entities=("Pro",)),
        memory("b1", MemoryType.BEHAVIOR, "The customer uses the Campaign Builder feature.",
               days=40, entities=("Campaign Builder",)),
    ]


def test_outcomes_link_back_to_what_preceded_them():
    links = infer(story())
    caused = [link for link in links if link.link_type is LinkType.CAUSED_BY]
    assert {link.target_memory_id for link in caused} == {"p1", "p2"}
    assert all(link.source_memory_id == "s1" for link in caused)
    assert all(link.rationale for link in caused)


def test_closer_causes_score_higher_when_evidence_is_equal():
    """Recency raises a link's confidence; repeated evidence raises it too."""
    memories = [
        memory("far", MemoryType.PROBLEM, "Sync failed.", days=30, entities=("Shopify",)),
        memory("near", MemoryType.PROBLEM, "Sync failed again.", days=3, entities=("Shopify",)),
        memory("out", MemoryType.SUBSCRIPTION,
               "The customer downgraded from the Pro plan to the Starter plan.", days=1),
    ]
    links = {(link.source_memory_id, link.target_memory_id): link for link in infer(memories)}
    assert links[("out", "near")].confidence > links[("out", "far")].confidence

    # Evidence counts too: a twice-reported older problem can outrank a newer single report.
    weighted = {
        (link.source_memory_id, link.target_memory_id): link for link in infer(story())
    }
    assert weighted[("s1", "p1")].confidence > weighted[("s1", "p2")].confidence


def test_unrelated_old_behaviour_is_not_linked_as_a_cause():
    caused = [link for link in infer(story()) if link.link_type is LinkType.CAUSED_BY]
    assert "b1" not in {link.target_memory_id for link in caused}


def test_resolution_links_to_the_problem_it_closes():
    memories = [
        *story(),
        memory("r1", MemoryType.FACT, "The Shopify integration is working again.", days=0,
               entities=("Shopify",), attributes={"resolved": True}),
    ]
    resolved = [link for link in infer(memories) if link.link_type is LinkType.RESOLVED_BY]
    assert {link.target_memory_id for link in resolved} == {"p1", "p2"}


def test_chain_walks_back_from_the_outcome():
    links = infer(story())
    chain = chain_for("s1", links)
    assert {link.target_memory_id for link in chain} >= {"p1", "p2"}


def test_outcome_detection():
    assert is_outcome(story()[2])
    assert not is_outcome(story()[0])


# ------------------------------------------------------- link inference at scale


def _linkable(index: int, *, days_ago: float, type_: MemoryType, content: str) -> MemoryView:
    moment = utcnow() - timedelta(days=days_ago)
    return MemoryView(
        id=f"mem_{index}",
        type=type_,
        content=content,
        first_seen_at=moment,
        last_seen_at=moment,
        importance=0.5,
    )


def test_link_inference_is_bounded_per_memory():
    """The O(n²) limit this replaced: a burst must not become a million comparisons.

    Five hundred memories in one afternoon all fall inside every window, so the count cap
    is the only thing keeping the work linear.
    """
    burst = [
        _linkable(index, days_ago=1 - index / 1000, type_=MemoryType.PROBLEM,
                  content=f"the shopify sync failed on attempt {index}")
        for index in range(500)
    ]
    burst.append(
        _linkable(999, days_ago=0, type_=MemoryType.SUBSCRIPTION,
                  content="downgraded from the Pro plan to Starter")
    )

    links = infer(burst)
    # Every outcome links to at most MAX_LINKS_PER_MEMORY contributors, and no memory is
    # compared against more than MAX_LOOKBACK candidates.
    by_source: dict[str, int] = {}
    for link in links:
        by_source[link.source_memory_id] = by_source.get(link.source_memory_id, 0) + 1
    assert max(by_source.values()) <= MAX_LINKS_PER_MEMORY + RELATED_LOOKBACK


def test_windowing_does_not_change_what_a_small_history_produces():
    """The cap only bites on bursts; an ordinary customer's links are unchanged."""
    history = [
        _linkable(1, days_ago=30, type_=MemoryType.PROBLEM, content="the shopify sync keeps failing"),
        _linkable(2, days_ago=20, type_=MemoryType.FEEDBACK, content="this is really frustrating"),
        _linkable(3, days_ago=2, type_=MemoryType.SUBSCRIPTION,
                  content="downgraded from Pro to Starter because the sync never worked"),
    ]
    links = infer(history)
    caused = [link for link in links if str(link.link_type) == "caused_by"]
    assert {link.target_memory_id for link in caused} == {"mem_1", "mem_2"}


def test_a_resolution_cannot_reach_back_forever():
    """A message today did not resolve a problem from two years ago."""
    ancient = _linkable(1, days_ago=800, type_=MemoryType.PROBLEM,
                        content="the shopify sync is broken")
    recent = _linkable(2, days_ago=400, type_=MemoryType.PROBLEM,
                       content="the shopify sync is broken again")
    fix = _linkable(3, days_ago=399, type_=MemoryType.PROBLEM,
                    content="the shopify sync is resolved now")

    resolved = {
        link.target_memory_id
        for link in infer([ancient, recent, fix])
        if str(link.link_type) == "resolved_by"
    }
    assert "mem_2" in resolved
    assert "mem_1" not in resolved, "800 days is outside the resolution window"
