"""Goal lifecycle rules.

The hard part of goal tracking is not noticing a goal — extraction already does that — it
is noticing, weeks later, that the goal happened, in a message that never mentions it.
These tests pin both halves of that: what counts as evidence, and what does not.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from common.enums import GoalStatus, MemoryType
from common.time import utcnow
from memory_engine.goals import (
    GoalView,
    candidates,
    decide,
    duplicate_of,
    keywords_for,
    overlap,
    summarise,
)
from memory_engine.goals.tracker import STALE_AFTER_DAYS
from nlp.answer import MemoryView

NOW = utcnow()
SSO_GOAL = "we want to roll out SSO to the whole sales team by Q4"


def memory(id: str, type: MemoryType, content: str, *, age_days: float = 1) -> MemoryView:
    return MemoryView(
        id=id,
        type=type,
        content=content,
        first_seen_at=NOW - timedelta(days=age_days),
        last_seen_at=NOW - timedelta(days=age_days),
    )


def goal(
    *,
    status: GoalStatus = GoalStatus.OPEN,
    progress: float = 0.0,
    opened_days_ago: float = 40,
    last_signal_days_ago: float | None = None,
    statement: str = SSO_GOAL,
) -> GoalView:
    return GoalView(
        id="goal_1",
        statement=statement,
        keywords=keywords_for(statement),
        status=status,
        progress=progress,
        opened_at=NOW - timedelta(days=opened_days_ago),
        last_signal_at=NOW
        - timedelta(days=last_signal_days_ago if last_signal_days_ago is not None else opened_days_ago),
        memory_id="mem_goal",
    )


# ------------------------------------------------------------------- keywords


def test_the_framing_of_a_goal_is_not_part_of_the_goal():
    """"We want to" says nothing about what they want."""
    words = keywords_for(SSO_GOAL)
    assert "sso" in words
    assert "sales" in words
    assert "want" not in words
    assert "plan" not in words


def test_a_goal_with_nothing_to_match_on_is_not_tracked():
    vague = memory("mem_1", MemoryType.GOAL, "we want to grow", age_days=1)
    assert candidates([vague]) == []


def test_only_goal_memories_become_goals():
    memories = [
        memory("mem_1", MemoryType.GOAL, SSO_GOAL),
        memory("mem_2", MemoryType.PROBLEM, "SSO login fails for the sales team"),
    ]
    found = candidates(memories)
    assert [item.memory_id for item in found] == ["mem_1"]


def test_an_already_tracked_goal_is_not_opened_twice():
    memories = [memory("mem_1", MemoryType.GOAL, SSO_GOAL)]
    assert candidates(memories, tracked_memory_ids=["mem_1"]) == []


def test_a_restatement_is_recognised_as_the_same_goal():
    existing = goal()
    restated = candidates(
        [memory("mem_2", MemoryType.GOAL, "we still plan to roll out SSO across sales")]
    )[0]
    assert duplicate_of(restated, [existing]) is existing


def test_an_unrelated_goal_is_not_a_duplicate():
    existing = goal()
    other = candidates(
        [memory("mem_2", MemoryType.GOAL, "we want to cut our invoice processing time")]
    )[0]
    assert duplicate_of(other, [existing]) is None


# ----------------------------------------------------------------- transitions


def test_a_completion_phrase_closes_the_goal_without_naming_it():
    done = memory("mem_2", MemoryType.BEHAVIOR, "SSO is now live for the whole sales team", age_days=2)
    transition = decide(goal(), memories=[done], now=NOW)

    assert transition is not None
    assert transition.status == GoalStatus.ACHIEVED
    assert transition.progress == 1.0
    assert transition.closes
    assert transition.evidence["memory_id"] == "mem_2"
    assert "now live" in transition.reason


def test_related_activity_advances_without_closing():
    working = memory("mem_2", MemoryType.BEHAVIOR, "started configuring SSO for sales", age_days=5)
    transition = decide(goal(), memories=[working], now=NOW)

    assert transition is not None
    assert transition.status == GoalStatus.PROGRESSING
    assert 0 < transition.progress < 1
    assert not transition.closes


def test_giving_up_abandons_the_goal():
    dropped = memory(
        "mem_2", MemoryType.INTENT, "we decided against the SSO rollout for sales", age_days=3
    )
    transition = decide(goal(), memories=[dropped], now=NOW)

    assert transition is not None
    assert transition.status == GoalStatus.ABANDONED
    assert transition.closes


def test_unrelated_news_moves_nothing():
    unrelated = memory("mem_2", MemoryType.PROBLEM, "the invoice pdf is broken", age_days=1)
    assert decide(goal(last_signal_days_ago=1), memories=[unrelated], now=NOW) is None


def test_every_memory_saying_the_customer_is_not_evidence_for_every_goal():
    """Stored memories are in the third person; "the customer" matched them all to every goal."""
    stated = "The customer's goal is launch automation."
    assert keywords_for(stated) == ("launch", "automation")
    view = goal(statement=stated, opened_days_ago=5)
    downgrade = memory("m_down", MemoryType.SUBSCRIPTION, "The customer downgraded from the Pro plan to the Starter plan.")
    assert overlap(view.keywords, downgrade.content) == 0.0
    assert decide(view, memories=[downgrade], now=NOW) is None


def test_a_cue_is_a_whole_word_not_part_of_one():
    """"done" lemmatises to "do", which "downgraded" and "download" begin with; and the
    goal's own "launch" is not the cue "launched"."""
    view = goal(statement="We want to launch the automation download.", opened_days_ago=5)
    for text in (
        "The automation download launch documentation was updated.",
        "The automation download launch is delayed.",
    ):
        moved = decide(view, memories=[memory("m1", MemoryType.FACT, text)], now=NOW)
        assert moved is None or moved.status is not GoalStatus.ACHIEVED, text
    for text in ("The automation download launch is done.", "We launched the automation download."):
        finished = decide(view, memories=[memory("m2", MemoryType.FACT, text)], now=NOW)
        assert finished is not None and finished.status is GoalStatus.ACHIEVED, text


def test_evidence_from_before_the_goal_was_stated_is_ignored():
    """Last month's "we finished the rollout" cannot close a goal set this week."""
    earlier = memory("mem_2", MemoryType.BEHAVIOR, "SSO is live for the sales team", age_days=90)
    assert decide(goal(opened_days_ago=10, last_signal_days_ago=1), memories=[earlier], now=NOW) is None


def test_the_goal_statement_itself_is_not_its_own_evidence():
    statement = MemoryView(
        id="mem_goal",
        type=MemoryType.GOAL,
        content=SSO_GOAL,
        first_seen_at=NOW - timedelta(days=40),
        last_seen_at=NOW - timedelta(days=40),
    )
    assert decide(goal(last_signal_days_ago=1), memories=[statement], now=NOW) is None


def test_a_closure_beats_a_progress_signal_in_the_same_batch():
    memories = [
        memory("mem_2", MemoryType.BEHAVIOR, "setting up SSO for sales", age_days=10),
        memory("mem_3", MemoryType.BEHAVIOR, "SSO rollout complete across sales", age_days=2),
    ]
    transition = decide(goal(), memories=memories, now=NOW)
    assert transition.status == GoalStatus.ACHIEVED
    assert transition.evidence["memory_id"] == "mem_3"


def test_a_quiet_goal_stalls_rather_than_being_declared_dead():
    transition = decide(goal(last_signal_days_ago=STALE_AFTER_DAYS + 5), memories=[], now=NOW)
    assert transition is not None
    assert transition.status == GoalStatus.STALLED
    assert not transition.closes
    assert "no supporting evidence" in transition.reason


def test_a_goal_still_within_its_window_is_left_alone():
    assert decide(goal(last_signal_days_ago=3), memories=[], now=NOW) is None


def test_closed_goals_are_never_reopened():
    for status in (GoalStatus.ACHIEVED, GoalStatus.ABANDONED):
        done = memory("mem_2", MemoryType.BEHAVIOR, "SSO is now live for sales", age_days=1)
        assert decide(goal(status=status), memories=[done], now=NOW) is None


def test_progress_cannot_creep_to_completion_on_chatter_alone():
    current = goal(status=GoalStatus.PROGRESSING, progress=0.5, last_signal_days_ago=1)
    chatter = memory("mem_2", MemoryType.BEHAVIOR, "still working on the SSO sales rollout", age_days=0)
    transition = decide(current, memories=[chatter], now=NOW)
    assert transition is None or transition.progress < 1.0


# --------------------------------------------------------------------- overlap


@pytest.mark.parametrize(
    ("text", "floor"),
    [
        ("SSO is now live for the whole sales team", 0.5),
        ("the sales team can log in with SSO", 0.3),
        ("our billing invoices are wrong", 0.0),
    ],
)
def test_overlap_scores_what_a_sentence_is_about(text: str, floor: float):
    assert overlap(keywords_for(SSO_GOAL), text) >= floor


def test_overlap_of_nothing_is_zero():
    assert overlap((), "anything at all") == 0.0
    assert overlap(keywords_for(SSO_GOAL), "") == 0.0


def test_summary_line_counts_the_states():
    goals = [
        goal(),
        goal(status=GoalStatus.ACHIEVED),
        goal(status=GoalStatus.STALLED),
    ]
    line = summarise(goals)
    assert "2 in flight" in line  # open + stalled are both still live
    assert "1 reached" in line
    assert "1 stalled" in line
    assert summarise([]) == "No goals recorded."
