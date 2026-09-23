"""Which memories a project marks restricted, and how the rules are written.

The policy engine is pure, so the rules themselves can be pinned exactly here; whether the
API honours the verdict is an end-to-end question and lives in ``test_access_control.py``.
"""

from __future__ import annotations

import pytest

from memory_engine.policy import (
    MASK,
    MAX_PATTERN_LENGTH,
    MAX_RULES,
    Policy,
    PolicyError,
    compile_policy,
    mask,
    partition,
)


def policy(*rules: dict) -> Policy:
    return compile_policy(list(rules))


def restricted(compiled: Policy, content: str, memory_type: str = "fact") -> bool:
    return compiled.evaluate(content=content, memory_type=memory_type).restricted


# ------------------------------------------------------------------ empty policy


def test_no_policy_restricts_nothing():
    empty = compile_policy(None)
    assert empty.is_empty
    assert not restricted(empty, "anything at all")
    assert not restricted(compile_policy([]), "anything at all")


# -------------------------------------------------------------------- type rules


def test_a_type_rule_restricts_a_whole_class():
    compiled = policy({"kind": "type", "value": "feedback"})
    assert restricted(compiled, "the reporting is great", "feedback")
    assert not restricted(compiled, "the reporting is great", "problem")


def test_an_unknown_type_is_refused_when_written():
    with pytest.raises(PolicyError, match="not a memory type"):
        policy({"kind": "type", "value": "vibes"})


# -------------------------------------------------------------------- term rules


@pytest.mark.parametrize(
    ("rule_word", "text"),
    [
        ("salary", "they asked about their salary again"),
        ("salary", "salaries were discussed on the call"),   # y → ies
        ("bonus", "the bonus structure"),
        ("bonus", "bonuses were paid late"),                 # s → es
        ("lawsuit", "there is a lawsuit"),
        ("lawsuit", "two lawsuits are pending"),             # plain plural
    ],
)
def test_a_term_rule_survives_inflection(rule_word: str, text: str):
    """The gate must not fail open on a plural.

    The stemmer is inconsistent across inflections — "salary" stems to ``salary`` and
    "salaries" to ``salari`` — so a naive single-stem match would silently let the second
    one through.
    """
    assert restricted(policy({"kind": "term", "value": [rule_word]}), text)


def test_a_term_rule_does_not_fire_on_unrelated_text():
    compiled = policy({"kind": "term", "value": ["salary"], "label": "pay"})
    assert not restricted(compiled, "the dashboard is slow this morning")


def test_a_term_rule_accepts_a_list_or_a_comma_string():
    from_list = policy({"kind": "term", "value": ["legal", "lawsuit"]})
    from_string = policy({"kind": "term", "value": "legal, lawsuit"})
    assert restricted(from_list, "their legal team is involved")
    assert restricted(from_string, "their legal team is involved")


def test_an_empty_term_rule_is_refused():
    with pytest.raises(PolicyError, match="at least one word"):
        policy({"kind": "term", "value": []})


def test_a_term_rule_has_a_ceiling():
    with pytest.raises(PolicyError, match="At most"):
        policy({"kind": "term", "value": [f"word{index}" for index in range(200)]})


# ----------------------------------------------------------------- pattern rules


def test_a_pattern_rule_matches_case_insensitively():
    compiled = policy({"kind": "pattern", "value": r"case\s+no\.?\s*\d+"})
    assert restricted(compiled, "see Case No. 4471 for the details")
    assert restricted(compiled, "CASE NO 4471")
    assert not restricted(compiled, "no case was made")


def test_a_broken_pattern_is_refused_at_write_time():
    """The alternative is every later memory write raising, long after it was typed."""
    with pytest.raises(PolicyError, match="not a valid regular expression"):
        policy({"kind": "pattern", "value": "([unclosed"})


def test_an_enormous_pattern_is_refused():
    with pytest.raises(PolicyError, match="at most"):
        policy({"kind": "pattern", "value": "a" * (MAX_PATTERN_LENGTH + 1)})


# ----------------------------------------------------------------------- shape


def test_a_rule_needs_a_known_kind():
    with pytest.raises(PolicyError, match="not a rule kind"):
        policy({"kind": "telepathy", "value": "x"})


def test_rules_must_be_objects():
    with pytest.raises(PolicyError, match="must be an object"):
        compile_policy(["salary"])


def test_there_is_a_ceiling_on_rules():
    with pytest.raises(PolicyError, match="At most"):
        compile_policy([{"kind": "term", "value": [f"w{i}"]} for i in range(MAX_RULES + 1)])


def test_the_matching_rule_is_reported():
    compiled = policy(
        {"kind": "term", "value": ["salary"], "label": "compensation talk"},
        {"kind": "type", "value": "feedback"},
    )
    verdict = compiled.evaluate(content="about my salary", memory_type="fact")
    assert verdict.restricted
    assert verdict.reason == "compensation talk"


def test_a_label_is_generated_when_not_given():
    compiled = policy({"kind": "type", "value": "problem"})
    assert compiled.rules[0].label == "problem memories are restricted"


def test_rules_round_trip_through_their_stored_form():
    """Settings store the compiled shape, so recompiling it must produce the same rules.

    And crucially the *matching* must survive the round trip too — storing expanded stems
    instead of the written words would re-stem them on the next load and drift.
    """
    original = policy(
        {"kind": "term", "value": ["salary", "bonus"], "label": "pay"},
        {"kind": "pattern", "value": r"\bNDA\b"},
    )
    again = compile_policy([rule.as_dict() for rule in original.rules])
    assert [rule.as_dict() for rule in again.rules] == [
        rule.as_dict() for rule in original.rules
    ]
    assert restricted(again, "the bonus structure")
    assert restricted(again, "bonuses were paid")
    assert restricted(again, "we signed an NDA")


# ------------------------------------------------------------------- partition


class _Memory:
    def __init__(self, sensitivity: str) -> None:
        self.sensitivity = sensitivity


def test_partition_counts_what_it_withholds():
    """Silently shortening a list is the failure mode this avoids."""
    memories = [_Memory("normal"), _Memory("restricted"), _Memory("normal")]
    allowed, withheld = partition(memories, cleared=False)
    assert len(allowed) == 2
    assert withheld == 1


def test_a_cleared_reader_sees_everything():
    memories = [_Memory("normal"), _Memory("restricted")]
    allowed, withheld = partition(memories, cleared=True)
    assert len(allowed) == 2
    assert withheld == 0


def test_the_mask_does_not_leak_length():
    assert mask("a very long and highly sensitive sentence") == MASK
    assert mask("short") == MASK
