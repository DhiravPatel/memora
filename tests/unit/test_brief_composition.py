"""The customer brief's judgement, composed from parts (§26 5.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memory_engine.brief import (
    BriefParts,
    caution_lines,
    compose,
    headline,
    markdown,
    next_step,
    opt_out_words,
    talking_points,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def parts(**overrides) -> BriefParts:
    base = {
        "name": "Acme",
        "now": NOW,
        "customer_since": ago(240),
        "last_active": ago(1),
        "health": {"score": 54.4, "band": "at_risk"},
        "trajectory": "declining",
        "plan": "pro",
    }
    return BriefParts(**{**base, **overrides})


def problem(ident: str, content: str, *, days: float, reports: int = 1) -> dict:
    return {"id": ident, "content": content, "first_seen_at": ago(days), "evidence_count": reports}


def caution(action: str, decision: str, summary: str, *rules: str, evidence=()) -> dict:
    return {"action": action, "decision": decision, "summary": summary, "rules": list(rules), "evidence": list(evidence)}


# -------------------------------------------------------------------- headline


def test_the_headline_is_the_situation_in_a_few_sentences():
    text = headline(
        parts(
            open_problems=3,
            problems=[problem("p1", "The export fails.", days=12)],
            intents=[{"id": "i1", "content": "We may cancel.", "kinds": ["cancellation"]}],
            last_active=ago(20),
        )
    )
    assert text == (
        "Acme: at risk (54), declining. On the Pro plan, customer for 8 months. "
        "3 open problems; said they may cancel. No activity in 2 weeks."
    )


def test_the_headline_counts_every_open_problem_not_just_the_ones_shown():
    # Two are restricted and withheld from this reader; the count is still whole.
    assert "4 open problems" in headline(parts(open_problems=4, problems=[problem("p1", "A fails.", days=1)]))
    assert "No open problems" in headline(parts(open_problems=0))


def test_the_headline_leaves_out_what_it_does_not_know():
    text = headline(parts(health=None, trajectory="steady", plan=None, customer_since=None, last_active=None))
    assert text == "Acme. No open problems."
    assert headline(parts(customer_since=NOW, plan=None)).startswith("Acme: at risk (54), declining. A new customer.")


def test_the_most_urgent_intent_leads():
    both = [
        {"id": "i1", "content": "They want more seats.", "kinds": ["expansion"]},
        {"id": "i2", "content": "They may downgrade.", "kinds": ["downgrade"]},
    ]
    assert "talked about downgrading" in headline(parts(intents=both))
    assert "talked about expanding" in headline(parts(intents=both[:1]))


# --------------------------------------------------------------- talking points


def test_a_threat_to_leave_comes_first_and_is_not_quoted_twice():
    threat = problem("p1", "The customer will cancel if the export keeps failing.", days=12, reports=2)
    points = talking_points(
        parts(
            problems=[threat, problem("p2", "Invoices fail to send.", days=3), problem("p3", "SSO is slow.", days=1)],
            intents=[{"id": "p1", "content": threat["content"], "kinds": ["cancellation"]}],
        )
    )
    assert points[0] == (
        "They said they may leave: “The customer will cancel if the export keeps failing” "
        "(open 12 days, reported 2 times). Acknowledge it before anything else."
    )
    assert points[1] == "Still open after 3 days: “Invoices fail to send”."
    assert points[2] == "Still open after a day: “SSO is slow”."
    assert sum("will cancel" in point for point in points) == 1


def test_problems_weigh_by_reports_then_age():
    points = talking_points(
        parts(
            problems=[
                problem("p1", "Old but reported once.", days=40),
                problem("p2", "Reported four times.", days=3, reports=4),
                problem("p3", "Opened this morning.", days=0.1),
            ]
        )
    )
    assert points[0] == "Still open after 3 days, reported 4 times: “Reported four times”."
    assert points[1] == "Still open after 5 weeks: “Old but reported once”."


def test_a_problem_quote_drops_the_count_the_brief_already_gives():
    text = talking_points(parts(problems=[problem("p1", "The export fails. Reported 3 times since 04 Sep 2026.", days=20, reports=3)]))[0]
    assert text == "Still open after 2 weeks, reported 3 times: “The export fails”."


def test_changes_goals_next_step_and_channel():
    points = talking_points(
        parts(
            changes={
                "summary": "Since the last conversation (19 Sep 2026): a; b; c; d; e; f.",
                "items": [{"title": "x"}],
            },
            goals=[
                {"id": "g1", "statement": "Roll out to finance", "status": "stalled", "last_signal_at": ago(35)},
                {"id": "g2", "statement": "Automate payroll", "status": "achieved", "last_signal_at": ago(5)},
                {"id": "g3", "statement": "Old win", "status": "achieved", "last_signal_at": ago(90)},
            ],
            channel="email",
        ),
        {"action": "Resolve: The export fails.", "rationale": "Reported yesterday and still open."},
    )
    assert points == [
        "Since the last conversation (19 Sep 2026): a; b; c; d — and 2 more changes.",
        "Their goal “Roll out to finance” has had no progress in 5 weeks — ask how it is going.",
        "They achieved their goal “Automate payroll” — worth acknowledging.",
        "Next step — Resolve: The export fails (reported yesterday and still open).",
        "They prefer email.",
    ]


def test_expansion_is_only_raised_when_they_are_not_leaving():
    growing = {"id": "i1", "content": "They want twelve more seats.", "kinds": ["expansion"]}
    assert "They talked about expanding: “They want twelve more seats”." in talking_points(parts(intents=[growing]))
    leaving = {"id": "i2", "content": "They may cancel.", "kinds": ["cancellation"]}
    assert not any("expanding" in point for point in talking_points(parts(intents=[growing, leaving])))


def test_at_most_six_points():
    many = [problem(f"p{n}", f"Problem {n} fails.", days=n + 1) for n in range(8)]
    points = talking_points(
        parts(
            problems=many,
            intents=[{"id": "i1", "content": "They may cancel.", "kinds": ["cancellation"]}],
            changes={"summary": "In the last 7 days: a.", "items": [{}]},
            goals=[{"id": "g1", "statement": "Go live", "status": "stalled", "last_signal_at": ago(40)}],
            channel="phone",
        ),
        {"action": "Call them", "rationale": ""},
    )
    assert len(points) == 6


# -------------------------------------------------------------------- cautions


def test_cautions_group_by_reason_and_leave_out_standing_policy():
    lines = caution_lines(
        [
            caution("offer_upgrade", "deny", "The customer has 3 open problems; resolve them before selling.", "open_problem_blocks_selling", evidence=["p1"]),
            caution("contact_customer", "deny", "The customer asked not to be contacted.", "respect_opt_out", evidence=["m1"]),
            caution("call_customer", "deny", "The customer asked not to be contacted.", "respect_opt_out", evidence=["m1", "m2"]),
            caution("send_email", "deny", "The customer asked not to be contacted.", "respect_opt_out"),
            caution("offer_discount", "require_approval", "A discount needs a person's approval.", "money_requires_approval"),
            caution("send_marketing", "allow", "Allowed: no rule objects."),
        ]
    )
    assert [line["text"] for line in lines] == [
        "Don't offer an upgrade: the customer has 3 open problems; resolve them before selling.",
        "Don't reach out unprompted, call them or email them: the customer asked not to be contacted.",
    ]
    assert lines[1]["actions"] == ["contact_customer", "call_customer", "send_email"]
    assert lines[1]["evidence"] == ["m1", "m2"]


def test_refusals_come_before_approvals_and_closing_a_ticket_reads_for_a_brief():
    lines = caution_lines(
        [
            caution("close_ticket", "require_approval", "The customer has 2 open problems and the request does not say which one.", "unresolved_problem_blocks_closing"),
            caution("offer_discount", "require_approval", "Discounts need a manager for this customer.", "project:vip"),
            caution("offer_upgrade", "deny", "This agent's profile does not allow offer_upgrade.", "profile"),
        ],
        open_problems=2,
    )
    assert [line["text"] for line in lines] == [
        "Don't offer an upgrade: this agent's profile does not allow offer_upgrade.",
        "Confirm the problem is fixed before you close their ticket: 2 problems still open.",
        "Ask a person before you offer a discount: discounts need a manager for this customer.",
    ]


def test_the_next_step_is_never_what_a_caution_forbids():
    call = {"key": "retention_outreach", "action": "Get on a call about the renewal", "memory_ids": ["i1"]}
    fix = {"key": "resolve_open_problem", "action": "Resolve: The export fails.", "memory_ids": ["p1"]}
    refused = [caution("call_customer", "deny", "The customer asked not to be called.", "respect_opt_out")]
    step, set_aside = next_step(parts(recommendations=[call, fix], cautions=refused))
    assert step is fix
    assert set_aside == [{"key": "retention_outreach", "action": "Get on a call about the renewal", "because": "The customer asked not to be called."}]
    # With nothing refused, the most urgent one stands.
    assert next_step(parts(recommendations=[call, fix]))[0] is call
    assert next_step(parts(recommendations=[call], cautions=refused)) == (None, set_aside)


def test_compose_returns_every_judgement():
    judged = compose(parts(recommendations=[{"key": "k", "action": "Do it"}]))
    assert set(judged) == {"headline", "talking_points", "cautions", "next_step", "set_aside"}
    assert judged["next_step"]["action"] == "Do it"


# -------------------------------------------------------------------- markdown


def test_markdown_is_a_page_with_every_section():
    brief = {
        "customer": {"name": "Acme", "external_id": "acme", "customer_since": ago(240), "last_active_at": ago(1)},
        "headline": "Acme: at risk (54), declining.",
        "situation": {
            "health": {"score": 54.4, "band": "at_risk", "churn_risk": 0.61, "trajectory": "declining"},
            "plan": {"name": "pro", "statement": "The customer upgraded to the Pro plan."},
            "lifecycle": [{"label": "Lifecycle", "state": "at_risk", "reasons": ["said they may cancel"]}],
        },
        "talking_points": ["Point one."],
        "cautions": [{"text": "Don't call them: the customer asked not to be called."}],
        "open_issues": [{"content": "The export fails.", "age_days": 12, "times_reported": 2}, {"content": "New.", "age_days": 0}],
        "goals": [{"statement": "Go live", "status": "progressing", "progress": 0.4}],
        "preferences": {"channel": "email", "opt_outs": [{"kind": "phone", "words": "asked not to be called"}], "statements": [{"content": "Email only."}]},
        "risks": [{"key": "churn_language", "label": "talking about leaving", "rationale": "cancellation language yesterday"}],
        "opportunities": [],
        "recent_changes": {"summary": "Since the last conversation: a new problem.", "items": [{"detected_at": ago(2), "title": "Invoices fail"}]},
        "last_conversation": {"summary": "[withheld]", "agent": "support-bot", "closed_at": ago(5)},
        "next_step": {"action": "Resolve: The export fails.", "rationale": "Reported 12 days ago and still open."},
        "set_aside": [{"action": "Get on a call", "because": "The customer asked not to be called."}],
        "withheld": 1,
        "generated_at": NOW,
    }
    page = markdown(brief)
    assert page.startswith("# Acme\n\nAcme: at risk (54), declining.\n")
    for section in ("## Situation", "## Talk about", "## Don't", "## Open issues", "## Goals", "## What they prefer", "## Signals", "## What changed", "## Last conversation", "## Next step"):
        assert section in page, section
    assert "- **Health:** 54/100 (at risk), declining; churn risk 61%" in page
    assert "- **Account:** customer since 2026-01-27, last active a day ago" in page
    assert "- “The export fails” — open 12 days, reported 2 times" in page
    assert "- “New” — opened today" in page
    assert "- “Go live” (progressing, 40%)" in page
    assert "- Asked not to be called." in page
    assert "- Risk: talking about leaving — cancellation language yesterday" in page
    assert "- 2026-09-22 Invoices fail" in page
    assert "_The summary quotes something your key may not read._ (support-bot, 5 days ago)" in page
    assert "_Set aside: Get on a call — the customer asked not to be called._" in page
    assert page.rstrip().endswith("_1 memory withheld from this brief for your key._")
    assert markdown({**brief, "withheld": 3}).rstrip().endswith("_3 memories withheld from this brief for your key._")


def test_a_withheld_statement_is_named_not_quoted():
    point = talking_points(parts(problems=[problem("p1", "[withheld]", days=3)]))[0]
    assert point == "Still open after 3 days: a statement you may not read."


def test_opt_outs_in_words():
    assert opt_out_words(["phone", "marketing"]) == [
        {"kind": "phone", "words": "asked not to be called"},
        {"kind": "marketing", "words": "opted out of marketing"},
    ]


def test_a_recent_plan_change_is_news_and_a_cancelled_plan_is_not_the_plan_they_are_on():
    downgraded = parts(
        plan="starter", plan_direction="downgraded", previous_plan="pro", plan_changed_days=5,
        plan_statement="The customer downgraded from the Pro plan to the Starter plan, citing: the sync never worked.",
    )
    assert "On the Starter plan (downgraded from Pro 5 days ago), customer for 8 months." in headline(downgraded)
    assert talking_points(downgraded)[0] == (
        "They downgraded from Pro to Starter 5 days ago: “The customer downgraded from the Pro plan to the "
        "Starter plan, citing: the sync never worked”."
    )
    # A month on, the plan is just the plan.
    settled = parts(plan="starter", plan_direction="downgraded", previous_plan="pro", plan_changed_days=45)
    assert "On the Starter plan, customer for 8 months." in headline(settled)
    assert not any(point.startswith("They downgraded") for point in talking_points(settled))

    cancelled = parts(plan="pro", plan_direction="cancelled", plan_changed_days=1)
    assert "Cancelled the Pro plan yesterday, customer for 8 months." in headline(cancelled)
    assert "On the Pro plan" not in headline(cancelled)
    assert talking_points(cancelled)[0] == "They cancelled the Pro plan yesterday."
    upgraded = parts(plan="pro", plan_direction="upgraded", previous_plan="starter", plan_changed_days=0)
    assert "On the Pro plan (upgraded from Starter today)" in headline(upgraded)
    assert not any("upgraded" in point for point in talking_points(upgraded)), "good news is not a talking point"


def test_the_page_says_how_many_open_issues_it_did_not_list():
    brief = {
        "customer": {"name": "Acme"},
        "headline": "Acme.",
        "situation": {"open_problems": 8},
        "open_issues": [{"content": "One.", "age_days": 1}, {"content": "Two.", "age_days": 2}],
    }
    assert "- “Two” — open 2 days\n- …and 6 more" in markdown(brief)


def test_channels_read_as_people_write_them():
    from memory_engine.brief import channel_name

    assert [channel_name(value) for value in ("whatsapp", "sms", "email", "in-app", None)] == ["WhatsApp", "SMS", "email", "in-app", None]
    assert talking_points(parts(channel=channel_name("whatsapp")))[-1] == "They prefer WhatsApp."



def test_drift_is_raised_as_a_question_and_replaces_the_still_open_line():
    flags = [
        {"id": "d1", "kind": "channel", "stated": "email", "observed": "whatsapp", "counts": {"observed": 6, "total": 7}, "memory_id": "m9"},
        {"id": "d2", "kind": "plan", "stated": "pro", "observed": "enterprise", "counts": {"events": 2}, "memory_id": "m8"},
        {"id": "d3", "kind": "quiet_problem", "stated": "The export times out.", "counts": {"quiet_days": 45}, "memory_id": "p1"},
        {"id": "d4", "kind": "usage", "stated": "Campaign Builder", "counts": {"quiet_days": 75}, "memory_id": "m7"},
    ]
    points = talking_points(parts(problems=[problem("p1", "The export times out.", days=45)], drift=flags))
    assert points[:4] == [
        "They said they prefer email, but 6 of their 7 contacts since came through WhatsApp — ask which they prefer now.",
        "Memory says the Pro plan, but their last 2 billing events were for Enterprise — check which plan they are on.",
        "“The export times out” has not come up in 6 weeks while they stayed active — ask whether it is fixed.",
        "They have not used the Campaign Builder in 2 months while staying active — ask what changed.",
    ]
    assert not any(point.startswith("Still open") for point in points)
    page = markdown({"customer": {"name": "Acme"}, "headline": "Acme.", "drift": [{"summary": "Mostly WhatsApp."}]})
    assert "## Possibly out of date\n- Mostly WhatsApp." in page
