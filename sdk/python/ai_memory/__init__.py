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

# Before a call: what to raise, and what not to do.
brief = memory.brief("cus_123")
print(brief.headline)
for caution in brief.cautions:
    print("-", caution.text)

# Before an agent acts: may it?
check = memory.check_action("cus_123", "offer_upgrade")
if not check:
    print(check.decision, check.summary)
```
"""

from ai_memory.agent import AsyncMemoryAgent, MemoryAgent, TurnContext
from ai_memory.client import AsyncMemoryClient, MemoryClient
from ai_memory.errors import (
    ActionDenied,
    ApprovalRequired,
    MemoryAPIError,
    MemoryConfigError,
    MemoryError,
    MemoryTimeoutError,
)
from ai_memory.models import (
    ActionCheck,
    AgentAction,
    AgentProfile,
    AgentRun,
    AgentSession,
    Approval,
    BriefCaution,
    Change,
    ConditionResult,
    Customer,
    Customer360,
    CustomerBrief,
    CustomerChanges,
    CustomerContext,
    EventExplanation,
    Goal,
    Health,
    LifecycleState,
    Memory,
    MemoryPlan,
    PriorSession,
    QueryResult,
    Recommendation,
    RunExplanation,
    RunTrace,
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
    "ActionCheck",
    "ActionDenied",
    "AgentAction",
    "AgentProfile",
    "AgentRun",
    "AgentSession",
    "Approval",
    "ApprovalRequired",
    "AsyncMemoryAgent",
    "AsyncMemoryClient",
    "BriefCaution",
    "Change",
    "Customer",
    "CustomerBrief",
    "CustomerChanges",
    "LifecycleState",
    "ConditionResult",
    "Customer360",
    "EventExplanation",
    "MemoryPlan",
    "CustomerContext",
    "Goal",
    "Health",
    "Memory",
    "MemoryAPIError",
    "MemoryAgent",
    "MemoryClient",
    "MemoryConfigError",
    "MemoryError",
    "MemoryTimeoutError",
    "PriorSession",
    "QueryResult",
    "Recommendation",
    "RunExplanation",
    "RunTrace",
    "SessionContext",
    "Signal",
    "SignalReport",
    "TrackedEvent",
    "Turn",
    "TurnContext",
    "TurnResult",
    "WebhookEvent",
    "WebhookVerificationError",
    "__version__",
    "construct_event",
    "verify_signature",
]
