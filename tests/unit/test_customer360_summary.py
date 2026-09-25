"""Customer 360's one-line summary names the plan rather than hinting at one."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.customer360_service import Customer360, _summarise

AT = datetime(2026, 9, 24, tzinfo=UTC)


def view(**sections) -> Customer360:
    return Customer360(customer={"name": "Acme", "external_id": "acme"}, generated_at=AT, sections=sections)


def test_the_summary_names_the_current_plan():
    text = _summarise(
        view(
            health={"score": 54.2, "band": "at_risk"},
            active_problems=[{"id": "p1"}, {"id": "p2"}],
            subscription={"content": "The customer upgraded from the Starter plan to the Pro plan."},
        )
    )
    assert text == "Acme: health 54/100 (at_risk), 2 open problems, on the Pro plan."


def test_a_statement_that_names_no_plan_is_still_mentioned():
    assert _summarise(view(subscription={"content": "The customer cancelled."})) == "Acme: a recorded subscription change."
    assert _summarise(view()) == "Nothing recorded for this customer yet."
