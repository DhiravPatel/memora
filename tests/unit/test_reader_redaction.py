"""The pure pieces behind "what may this reader see" (§26 3.1): no database needed."""

from __future__ import annotations

from datetime import UTC, datetime

from common.enums import ApiKeyScope, SignalDirection, Trajectory
from database.access import UNRESTRICTED, access_for_types, access_scope, current_access
from memory_engine.analytics.recommend import Recommendation
from memory_engine.analytics.signals import Signal, SignalReport, mask_quotes
from memory_engine.facts import CustomerFacts
from memory_engine.guardrails import redact_reason
from memory_engine.policy import WITHHELD


def test_an_empty_type_list_means_every_type():
    assert access_for_types([]).readable_types is None
    assert access_for_types(None).restricts_types is False
    access = access_for_types(["Problem", " feedback "], profile="support")
    assert access.readable_types == frozenset({"problem", "feedback"})
    assert access.can_read("problem") and not access.can_read("goal")


def test_access_is_scoped_and_restored():
    assert current_access() is UNRESTRICTED
    with access_scope(access_for_types(["problem"])):
        assert current_access().restricts_types
    assert current_access() is UNRESTRICTED


def test_admin_confers_neither_clearance_nor_deciding_approvals():
    assert ApiKeyScope.APPROVALS_DECIDE in ApiKeyScope.not_implied_by_admin()
    assert ApiKeyScope.MEMORY_RESTRICTED in ApiKeyScope.not_implied_by_admin()


def test_mask_quotes_replaces_only_hidden_quotes():
    text = "Resolve: Salary payments fail; see also Shopify sync"
    quotes = (("mem_a", "Salary payments fail"), ("mem_b", "Shopify sync"))
    assert mask_quotes(text, quotes, {"mem_a"}) == f"Resolve: {WITHHELD}; see also Shopify sync"
    assert mask_quotes(text, quotes, set()) == text


def test_a_recommendation_keeps_its_advice_and_loses_the_quote():
    action = Recommendation(
        key="resolve_open_problem",
        action="Resolve: Salary payments fail",
        rationale="Reported 3 days ago and still open.",
        category="support",
        urgency=0.8,
        memory_ids=("mem_a", "mem_b"),
        quotes=(("mem_a", "Salary payments fail"),),
    )
    shown = action.redacted(frozenset({"mem_a"}))
    assert shown.action == f"Resolve: {WITHHELD}"
    assert shown.memory_ids == ("mem_b",)
    assert shown.urgency == action.urgency and shown.key == action.key
    assert action.redacted(frozenset({"mem_z"})) is action


def test_a_signal_report_keeps_its_numbers():
    report = SignalReport(
        trajectory=Trajectory.DECLINING,
        churn_risk=0.7,
        expansion_score=0.1,
        confidence=0.6,
        signals=[
            Signal(
                key="goal_stalled",
                label="a stated goal has stalled",
                direction=SignalDirection.RISK,
                strength=0.5,
                horizon_days=45,
                rationale="no progress on “Launch in Europe” for 40 days",
                quotes=(("goal_1", "Launch in Europe"),),
            )
        ],
        computed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    shown = report.redacted(frozenset({"goal_1"}))
    assert shown.churn_risk == 0.7 and shown.trajectory == Trajectory.DECLINING
    assert "Launch in Europe" not in shown.signals[0].rationale
    assert WITHHELD in shown.headline


def test_facts_redact_for_an_explicit_hidden_set():
    facts = CustomerFacts(
        values={"problems.entities": ["shopify", "payroll"], "problems.open_count": 2},
        evidence={"problems.entities": ["mem_a", "mem_b"], "problems.open_count": ["mem_a", "mem_b"]},
        evidence_by_value={"problems.entities": {"shopify": ["mem_a"], "payroll": ["mem_b"]}},
    )
    shown = facts.redacted({"mem_b"})
    assert shown.values["problems.entities"] == ["shopify"]
    assert shown.values["problems.open_count"] == 2  # a number is not a quote
    assert shown.hidden_ids == frozenset({"mem_b"})
    assert "mem_b" not in shown.evidence["problems.entities"]
    assert facts.cited_ids() == {"mem_a", "mem_b"}
    # With nothing to hide, it is the same document.
    assert facts.redacted(set()) is facts


def test_a_reason_is_generalised_only_when_what_it_quotes_is_hidden():
    reason = {
        "rule": "channel_preference",
        "decision": "deny",
        "explanation": "The customer prefers whatsapp, not email.",
        "redacted_explanation": "The customer's contact preference does not allow email.",
        "evidence": ["mem_pref"],
        "evaluation": None,
    }
    assert redact_reason(reason, {"mem_other"})["explanation"] == reason["explanation"]
    hidden = redact_reason(reason, {"mem_pref"})
    assert hidden["explanation"] == reason["redacted_explanation"]
    assert hidden["evidence"] == []
