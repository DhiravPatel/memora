"""The arithmetic of retrieval evaluation, pinned exactly."""

from __future__ import annotations

import pytest

from memory_engine.evaluation import (
    CaseSpec,
    Retrieved,
    aggregate,
    compare,
    phrase_matches,
    score_case,
)


def retrieved(*items: tuple[str, str], cited: tuple[str, ...] = ()) -> list[Retrieved]:
    return [Retrieved(id=ident, content=content, cited=ident in cited) for ident, content in items]


RESULTS = retrieved(
    ("mem_a", "The Shopify sync fails during checkout."),
    ("mem_b", "The customer prefers WhatsApp."),
    ("mem_c", "Invoices were charged twice."),
    cited=("mem_c",),
)


def test_an_expected_memory_is_scored_by_its_rank():
    result = score_case(CaseSpec("c1", "billing?", expected_ids=["mem_c"]), RESULTS)
    assert result.first_rank == 3
    assert result.reciprocal_rank == pytest.approx(1 / 3)
    assert result.recall_at(1) == 0.0
    assert result.recall_at(3) == 1.0
    assert result.cited_hit is True


def test_a_phrase_matches_any_form_of_every_word():
    """Survives reprocessing: the new memory still says the same thing."""
    assert phrase_matches("sync failures", "The Shopify sync fails during checkout.")
    assert phrase_matches("charge twice", "Invoices were charged twice.")
    assert not phrase_matches("refund", "Invoices were charged twice.")


def test_recall_counts_each_expected_item():
    result = score_case(
        CaseSpec("c1", "?", expected_ids=["mem_a"], expected_phrases=["refund issued"]),
        RESULTS,
    )
    assert result.recall_at(10) == 0.5
    assert result.hit_at(1) is True


def test_a_miss_contributes_zero_everywhere():
    result = score_case(CaseSpec("c1", "?", expected_ids=["mem_zzz"]), RESULTS)
    assert result.first_rank is None
    assert result.reciprocal_rank == 0.0
    assert result.cited_hit is False


def test_aggregate_is_a_macro_average():
    results = [
        score_case(CaseSpec("c1", "?", expected_ids=["mem_a"]), RESULTS),  # rank 1
        score_case(CaseSpec("c2", "?", expected_ids=["mem_c"]), RESULTS),  # rank 3
        score_case(CaseSpec("c3", "?", expected_ids=["mem_zzz"]), RESULTS),  # miss
    ]
    metrics = aggregate(results)
    assert metrics["recall"]["@1"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["recall"]["@3"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["mrr"] == pytest.approx((1 + 1 / 3 + 0) / 3, abs=1e-4)
    assert metrics["citation_hit_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert metrics["misses"] == ["c3"]


def test_a_case_that_errored_is_counted_but_not_scored():
    ok = score_case(CaseSpec("c1", "?", expected_ids=["mem_a"]), RESULTS)
    broken = score_case(CaseSpec("c2", "?", expected_ids=["mem_a"]), [])
    broken.error = "customer no longer exists"
    metrics = aggregate([ok, broken])
    assert metrics["cases"] == 2 and metrics["scored"] == 1 and metrics["errors"] == 1
    assert metrics["recall"]["@1"] == 1.0


def test_a_regression_is_flagged_even_when_the_average_rises():
    """The headline number can improve while a question that mattered starts failing."""
    baseline = {"recall": {"@5": 0.5}, "hit": {"@5": 0.5}, "mrr": 0.4, "citation_hit_rate": 0.5, "misses": ["c2"]}
    current = {"recall": {"@5": 0.6}, "hit": {"@5": 0.6}, "mrr": 0.5, "citation_hit_rate": 0.6, "misses": ["c7"]}
    deltas = compare(current, baseline)
    assert deltas["recall"]["@5"] == pytest.approx(0.1)
    assert deltas["newly_missed"] == ["c7"]
    assert deltas["newly_found"] == ["c2"]
    assert deltas["regressed"] is True


def test_no_baseline_means_no_comparison():
    assert compare({"recall": {}}, None) is None
