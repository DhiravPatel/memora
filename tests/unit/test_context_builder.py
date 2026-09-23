"""Context building: sectioning, deduplication and the token budget."""

from __future__ import annotations

from types import SimpleNamespace

from common.enums import MemoryType
from common.time import utcnow
from memory_engine.context.builder import ContextBuilder
from memory_engine.schemas import ScoredMemory


def memory(content: str, memory_type=MemoryType.PROBLEM, memory_id="mem_1"):
    return SimpleNamespace(
        id=memory_id,
        type=memory_type,
        content=content,
        importance=0.8,
        confidence=0.9,
        last_seen_at=utcnow(),
        evidence_count=2,
        source_event_ids=["evt_1"],
    )


def scored(content: str, memory_type=MemoryType.PROBLEM, memory_id="mem_1", score=0.9):
    item = ScoredMemory(memory=memory(content, memory_type, memory_id))
    item.score = score
    return item


def test_memories_are_grouped_into_sections():
    context = ContextBuilder().build(
        customer={"id": "cus_1", "name": "John"},
        memories=[
            scored("Customer cannot connect Shopify.", MemoryType.PROBLEM, "mem_1"),
            scored("Customer prefers WhatsApp.", MemoryType.PREFERENCE, "mem_2"),
            scored("Customer is on the Pro plan.", MemoryType.SUBSCRIPTION, "mem_3"),
        ],
    )
    payload = context.to_dict()
    assert payload["active_problems"] == ["Customer cannot connect Shopify."]
    assert payload["preferences"] == ["Customer prefers WhatsApp."]
    assert "Customer is on the Pro plan." in payload["important_facts"]
    assert len(context.memory_ids) == 3


def test_near_duplicates_are_dropped():
    context = ContextBuilder().build(
        customer={"id": "cus_1"},
        memories=[
            scored("Customer cannot connect Shopify.", memory_id="mem_1"),
            scored("Customer cannot connect Shopify", memory_id="mem_2"),
        ],
    )
    assert len(context.memory_ids) == 1


def test_token_budget_truncates_instead_of_overflowing():
    long_text = "Customer reported a very detailed problem. " * 20
    memories = [
        scored(f"{long_text} variant {index}", memory_id=f"mem_{index}") for index in range(10)
    ]
    context = ContextBuilder(token_budget=200).build(
        customer={"id": "cus_1"}, memories=memories
    )
    assert context.truncated
    assert context.token_count <= 200
    assert len(context.memory_ids) < 10


def test_prompt_text_rendering_is_readable():
    context = ContextBuilder().build(
        customer={"id": "cus_1", "name": "John"},
        memories=[scored("Customer cannot connect Shopify.")],
        recent_events=[{"occurred_at": "2026-08-09", "event_type": "support_message"}],
        relationships=[{"source": "John", "type": "uses", "target": "Shopify"}],
    )
    text = context.to_prompt_text()
    assert "Customer: John" in text
    assert "Active Problems:" in text
    assert "- Customer cannot connect Shopify." in text
    assert "John uses Shopify" in text


def test_per_section_cap_applies():
    memories = [scored(f"Problem number {index}.", memory_id=f"mem_{index}") for index in range(10)]
    context = ContextBuilder(max_per_section=3).build(customer={"id": "c"}, memories=memories)
    assert len(context.sections["active_problems"]) == 3
    assert context.truncated
