"""ai-memory — Python SDK for the AI Memory Layer.

```python
from ai_memory import MemoryClient

memory = MemoryClient(api_key="mk_live_…", base_url="https://memory.internal")

memory.track(
    customer_id="cus_123",
    type="support_message",
    data={"message": "Shopify sync has failed three times this week"},
    external_event_id="msg_456",
)

context = memory.context("cus_123", task="support_response", as_text=True)
print(context.prompt_text)

for action in memory.recommendations("cus_123"):
    print(action.priority, action.action, "—", action.rationale)
```
"""

from ai_memory.client import AsyncMemoryClient, MemoryClient
from ai_memory.errors import (
    MemoryAPIError,
    MemoryConfigError,
    MemoryError,
    MemoryTimeoutError,
)
from ai_memory.models import (
    AgentSession,
    Customer,
    Customer360,
    CustomerContext,
    EventExplanation,
    Goal,
    Health,
    Memory,
    MemoryPlan,
    PriorSession,
    QueryResult,
    Recommendation,
    SessionContext,
    Signal,
    SignalReport,
    TrackedEvent,
    Turn,
    TurnResult,
)
from ai_memory.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookEvent,
    WebhookVerificationError,
    construct_event,
    verify_signature,
)

__version__ = "0.1.0"

__all__ = [
    "DELIVERY_HEADER",
    "EVENT_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "AgentSession",
    "AsyncMemoryClient",
    "Customer",
    "Customer360",
    "EventExplanation",
    "MemoryPlan",
    "CustomerContext",
    "Goal",
    "Health",
    "Memory",
    "MemoryAPIError",
    "MemoryClient",
    "MemoryConfigError",
    "MemoryError",
    "MemoryTimeoutError",
    "PriorSession",
    "QueryResult",
    "Recommendation",
    "SessionContext",
    "Signal",
    "SignalReport",
    "TrackedEvent",
    "Turn",
    "TurnResult",
    "WebhookEvent",
    "WebhookVerificationError",
    "__version__",
    "construct_event",
    "verify_signature",
]
