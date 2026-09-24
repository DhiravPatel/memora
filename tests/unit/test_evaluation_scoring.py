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


# ------------------------------------------------------------ extraction (§26 4.3)

from memory_engine.evaluation import (  # noqa: E402
    Expectation,
    ExtractionSpec,
    aggregate_extraction,
    passing,
    score_extraction,
)

PLANNED = [
    {"content": "The Shopify sync fails during checkout.", "type": "problem", "action": "create",
     "sensitivity": "normal", "entities": ["Shopify"]},
    {"content": "The customer prefers WhatsApp.", "type": "fact", "action": "create", "sensitivity": "normal", "entities": []},
]


def test_an_extraction_case_passes_when_every_expectation_holds():
    spec = ExtractionSpec(
        "c1",
        "support message",
        expect=[Expectation(type="problem", contains="shopify sync fail", entity="shopify", action="create")],
        forbid=[Expectation(type="intent")],
    )
    result = score_extraction(spec, PLANNED)
    assert result.passed
    assert result.expected[0]["matched"] and result.expected[0]["memory_index"] == 0
    assert result.unexpected == [1]  # reported, not failed: the case did not forbid it


def test_a_near_miss_says_what_was_wrong():
    spec = ExtractionSpec("c2", "preference", expect=[Expectation(type="preference", contains="prefers whatsapp")])
    result = score_extraction(spec, PLANNED)
    assert not result.passed
    assert result.expected[0]["near_miss"] == "typed fact, expected preference"
    missing = score_extraction(ExtractionSpec("c3", "x", expect=[Expectation(contains="refund")]), PLANNED)
    assert missing.expected[0]["near_miss"] == "nothing said it"
    stopped = score_extraction(ExtractionSpec("c4", "x", expect=[Expectation(contains="refund")]), [], stop_reason="Below the threshold.")
    assert stopped.expected[0]["near_miss"] == "Below the threshold."


def test_forbidden_memories_and_expecting_nothing_are_false_memories():
    forbidden = score_extraction(ExtractionSpec("c5", "news", forbid=[Expectation(type="problem")]), PLANNED)
    assert not forbidden.passed and forbidden.false_memory and forbidden.forbidden[0]["violated_by"] == [0]
    quiet = score_extraction(ExtractionSpec("c6", "page view", expect_nothing=True), [])
    assert quiet.passed and not quiet.false_memory
    noisy = score_extraction(ExtractionSpec("c7", "page view", expect_nothing=True), PLANNED[:1])
    assert not noisy.passed and noisy.false_memory


def test_extraction_metrics_separate_recall_from_type_errors():
    results = [
        score_extraction(ExtractionSpec("a", "a", expect=[Expectation(type="problem", contains="shopify sync")]), PLANNED),
        score_extraction(ExtractionSpec("b", "b", expect=[Expectation(type="preference", contains="prefers whatsapp")]), PLANNED),
        score_extraction(ExtractionSpec("c", "c", expect=[Expectation(type="problem", contains="double charge")]), PLANNED),
        score_extraction(ExtractionSpec("d", "d", forbid=[Expectation(type="problem")]), PLANNED),
    ]
    metrics = aggregate_extraction(results)
    assert metrics["accuracy"] == 0.25
    assert metrics["expected_recall"] == round(1 / 3, 4)
    # Of the two statements that were extracted at all, one got its type wrong; the third
    # was never extracted, which is a recall miss, not a type error.
    assert metrics["type_accuracy"] == 0.5
    assert metrics["false_memory_rate"] == 0.25
    assert metrics["failing"] == ["b", "c", "d"]


def test_pass_fail_per_case_for_a_regression():
    extraction = score_extraction(ExtractionSpec("x1", "x", expect=[Expectation(contains="shopify")]), PLANNED).as_dict()
    retrieval = score_case(CaseSpec("r1", "billing?", expected_ids=["mem_c"]), RESULTS).as_dict()
    missed = score_case(CaseSpec("r2", "refunds?", expected_ids=["mem_z"]), RESULTS).as_dict()
    assert passing([extraction, retrieval, missed]) == {"x1": True, "r1": True, "r2": False}


def test_compare_flags_an_extraction_case_that_stopped_passing():
    before = {"recall": {}, "hit": {}, "misses": [], "extraction": {"accuracy": 1.0, "false_memory_rate": 0.0, "failing": []}}
    after = {"recall": {}, "hit": {}, "misses": [], "extraction": {"accuracy": 0.5, "false_memory_rate": 0.5, "failing": ["x1"]}}
    deltas = compare(after, before)
    assert deltas["regressed"] is True
    assert deltas["extraction"] == {"accuracy": -0.5, "false_memory_rate": 0.5, "newly_failing": ["x1"], "newly_passing": []}
