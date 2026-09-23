# @ai-memory/sdk

TypeScript client for the AI Memory Layer.

```bash
npm install @ai-memory/sdk
```

```ts
import { MemoryClient } from "@ai-memory/sdk";

const memory = new MemoryClient({
  apiKey: process.env.MEMORY_API_KEY!,
  baseUrl: process.env.MEMORY_API_URL, // optional, for self-hosted deployments
});

// Everything worth knowing, in one call — what an agent reads before it replies
const view = await memory.customers.get360("cus_123", {
  include: ["health", "active_problems", "goals"],
});
console.log(view.summary);

// Find out what an event would do, without sending it
const preview = await memory.events.preview({
  customerId: "cus_123",
  type: "support_message",
  data: { message: "I've tried connecting Shopify three times." },
});
if (!preview.wouldProcess) console.log(preview.stopReason);
preview.memories.forEach((plan) => console.log(plan.action, plan.type, plan.reason));

// Track what happened
await memory.events.track({
  customerId: "cus_123",
  type: "support_message",
  data: { message: "I've tried connecting Shopify three times but it still doesn't work." },
  externalEventId: "msg_456", // idempotency
});

// Ask what it means
const result = await memory.query({
  customerId: "cus_123",
  query: "What problems has this customer experienced?",
});
console.log(result.answer, result.sources);

// Give an agent context before it replies
const context = await memory.context({
  customerId: "cus_123",
  task: "support_response",
  format: "text",
});
console.log(context.promptText);
```

The API key identifies one project and must stay server-side.
