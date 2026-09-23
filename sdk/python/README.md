# ai-memory (Python)

```bash
pip install ai-memory
```

```python
from ai_memory import MemoryClient

memory = MemoryClient(api_key=os.environ["MEMORY_API_KEY"])

# Track what happened
memory.track(
    customer_id="cus_123",
    type="support_message",
    data={"message": "I've tried connecting Shopify three times but it still doesn't work."},
    external_event_id="msg_456",   # idempotent
)

# Find out what an event would do, without sending it
preview = memory.preview(
    customer_id="cus_123",
    type="support_message",
    data={"message": "I've tried connecting Shopify three times."},
)
if not preview:                      # nothing would be remembered
    print(preview.stop_reason)       # ...and this says why
for plan in preview.memories:
    print(plan.action, plan.type, plan.content, plan.reason)

# Everything worth knowing, in one call — what an agent reads before it replies
view = memory.customer_360("cus_123", include=["health", "active_problems", "goals"])
print(view.summary)
if view.is_at_risk:
    escalate(view.active_problems, view.recommended_actions)

# Ask what it means
result = memory.query("cus_123", "Why is this customer unhappy?", include_trace=True)
print(result.answer, result.source_event_ids)

# Give an agent context before it replies
context = memory.context("cus_123", task="support_response", as_text=True)
print(context.prompt_text)

# Health and churn risk, derived from the same memories
health = memory.health("cus_123")
if health.is_at_risk:
    escalate(health.explanation)
```

An async client with the same surface is available as `AsyncMemoryClient`.

The API key identifies exactly one project and must stay server-side.
