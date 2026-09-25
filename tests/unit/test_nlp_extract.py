"""Extraction: events in, memory statements out."""

from __future__ import annotations

from common.enums import EntityType, MemoryType
from nlp.entities import EntityHit
from nlp.extract import attribute, collect_segments, extract, is_filler, resolve_leading_pronoun


def contents(output) -> list[str]:
    return [memory.content for memory in output.memories]


def test_free_text_becomes_self_contained_third_person_statements():
    output = extract(
        event_type="support_message",
        data={"message": "Hi. I've tried connecting Shopify three times but it still doesn't work! Thanks"},
    )
    assert any(content.startswith("The Shopify integration") for content in contents(output))
    assert all(not content.lower().startswith("i ") for content in contents(output))
    # Greetings and sign-offs carry nothing durable.
    assert not any("thanks" in content.lower() for content in contents(output))


def test_structured_events_use_templates():
    output = extract(
        event_type="subscription_downgraded",
        data={"plan": "starter", "previous_plan": "pro", "reason": "Too expensive for us"},
    )
    templated = [memory for memory in output.memories if memory.source == "template"]
    assert templated
    assert "downgraded from the Pro plan to the Starter plan" in templated[0].content
    assert templated[0].type is MemoryType.SUBSCRIPTION


def test_reason_fields_are_attributed_to_the_customer():
    output = extract(
        event_type="subscription_downgraded",
        data={"plan": "starter", "previous_plan": "pro", "reason": "Integration never worked"},
    )
    prose = [memory for memory in output.memories if memory.source == "text"]
    assert prose and prose[0].content.startswith("The customer reported that")


def test_low_value_events_produce_nothing():
    output = extract(event_type="page_view", data={"path": "/dashboard"})
    assert output.memories == []
    assert output.stats["has_text"] is False


def test_clause_splitting_keeps_both_statements():
    output = extract(
        event_type="feedback_submitted",
        data={"feedback": "The reporting is great but exports are painfully slow"},
    )
    types = {memory.type for memory in output.memories}
    assert MemoryType.FEEDBACK in types
    assert len(output.memories) >= 2


def test_pronoun_resolution_uses_the_sentence_topic():
    topic = EntityHit(EntityType.INTEGRATION, "Shopify", rule="test")
    assert resolve_leading_pronoun("it keeps failing", topic) == "The Shopify integration keeps failing"
    assert resolve_leading_pronoun("Shopify keeps failing", None) == "Shopify keeps failing"


def test_entities_and_relationships_are_derived():
    output = extract(
        event_type="integration_failed",
        data={"integration": "shopify", "error": "OAuth handshake timed out"},
        customer_label="John",
    )
    names = {hit.name for hit in output.entities}
    assert "Shopify" in names
    assert any(source == "John" and target == "Shopify" for source, _, target, _ in output.relationships)


def test_memory_cap_is_respected():
    long_message = ". ".join(f"Problem number {index} keeps failing" for index in range(20))
    output = extract(event_type="support_message", data={"message": long_message}, max_memories=3)
    assert len(output.memories) == 3


def test_helpers():
    assert is_filler("thanks") and is_filler("hi") and not is_filler("Shopify keeps failing")
    assert attribute("integration never worked").startswith("The customer reported that")
    assert attribute("The customer prefers email") == "The customer prefers email"
    assert collect_segments({"message": "a", "reason": "b"}) == [("message", "a"), ("reason", "b")]


def test_a_threat_on_a_condition_is_an_intent_and_the_problem_it_hangs_on():
    output = extract(event_type="support_message", data={"message": "We will cancel if the payroll export keeps failing."})
    found = [(memory.type, memory.content, memory.rule) for memory in output.memories]
    assert (MemoryType.INTENT, "The customer will cancel if the payroll export keeps failing.", "text:intent:conditional_threat") in found
    assert (MemoryType.PROBLEM, "The payroll export keeps failing.", "text:problem:condition") in found

    first = extract(event_type="support_message", data={"message": "If the Shopify sync breaks again, we will switch to a competitor."})
    assert {memory.type for memory in first.memories} == {MemoryType.INTENT, MemoryType.PROBLEM}
    assert "The Shopify sync breaks again." in contents(first)


def test_a_demand_or_a_request_is_not_split():
    demand = extract(event_type="support_message", data={"message": "We will cancel unless you fix the export."})
    assert [(memory.type, memory.content) for memory in demand.memories] == [
        (MemoryType.INTENT, "The customer will cancel unless you fix the export.")
    ]
    request = extract(event_type="support_message", data={"message": "Please cancel the invoice if it was sent twice."})
    assert all(memory.type is not MemoryType.INTENT for memory in request.memories)
    assert not any(memory.rule.endswith("conditional_threat") for memory in request.memories)
