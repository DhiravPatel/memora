"""A closed session's summary, from what the customer said (§14)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.agent_service import compose_summary
from common.enums import TurnRole

AT = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)


def turn(content: str, role: TurnRole = TurnRole.USER) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, occurred_at=AT)


def test_a_question_keeps_its_question_mark():
    summary = compose_summary([turn("Is there any news on the payroll export?")], outcome="followed up")
    assert summary == "In a conversation on 2026-09-19, the customer said: Is there any news on the payroll export? Outcome: followed up."


def test_a_statement_without_a_full_stop_gets_one():
    summary = compose_summary([turn("The export failed again"), turn("Sorry about that", TurnRole.AGENT)])
    assert summary == "In a conversation on 2026-09-19, the customer said: The export failed again."
    assert compose_summary([turn("Thanks!", TurnRole.AGENT)]) is None
