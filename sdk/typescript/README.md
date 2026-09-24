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

// A rule, evaluated against a customer — the same language the lifecycle and guardrails use
const { matched } = await memory.state.evaluate("cus_123", 'problems.entities contains "billing"');
const state = await memory.state.current("cus_123"); // { state: "at_risk", reasons: [...], ... }
const engagement = await memory.state.current("cus_123", "engagement");

// What changed since the last conversation — typed changes, then and now
const { summary, changes, then, now } = await memory.state.changes("cus_123", {
  since: "last_session",
  agent: "support-bot",
});

// Is memory any good? Measure retrieval and extraction, and fail CI on a regression
const run = await memory.quality.runEval(setId, { label: "after vocabulary change" });
if (run.comparison?.regressed) process.exit(1);
// Before saving a setting: what would it break?
const check = await memory.quality.evalRegression(setId, { consolidation_similarity: 0.4 });
if (!check.safe) console.log(check.summary, check.newly_failing);

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

// Why did my agent do this? — including what it was not given, and why
const trace = await memory.runs.trace(result.runId!);
trace.ignored.forEach((item) => console.log("✗", item.reason, item.why));

// Give an agent context before it replies
const context = await memory.context({
  customerId: "cus_123",
  task: "support_response",
  format: "text",
});
console.log(context.promptText);
```

The API key identifies one project and must stay server-side.

## Agents

```ts
import { MemoryAgent, ActionDeniedError, ApprovalRequiredError } from "@ai-memory/sdk";

// Ask before acting
const check = await memory.checkAction({ customerId: "cus_123", action: "offer_upgrade" });
if (!check.allowed) console.log(check.decision, check.summary, check.evidence);

// A person decides; the agent redeems the approval once, for exactly this request
const approval = await memory.guardrails.waitForApproval(check.approval!.id, { timeoutMs: 60_000 });

// Why did the agent say that?
const answer = await memory.query({ customerId: "cus_123", query: "Is their Shopify sync fixed?" });
const why = await memory.runs.explain(answer.runId!);
console.log(why.narrative.join("\n"));

// The whole loop around your own model: brief, record, guard, summarise
const agent = new MemoryAgent(memory, { customerId: "cus_123", agent: "support-bot", conversationId: ticket.id });
await agent.run(async () => {
  const reply = await agent.respond(ticket.message, (prompt, message) => llm({ system: prompt, user: message }));
  await agent.guard("offer_discount", { amount: 20 }); // throws ActionDeniedError / ApprovalRequiredError
  // Through the gateway: request, wait for a person if needed, run, report done/failed
  await agent.perform("issue_credit", () => billing.credit(20), { amount: 20 }, { waitMs: 120_000 });
});

// Or by hand: the one call before acting, then report the outcome
const action = await memory.actions.request({ customerId: "cus_123", action: "process_refund", request: { amount: 25 } });
if (action.status === "allowed") await memory.actions.complete(action.id, "done", { externalRef: refundId });
```

`memory.profiles.me()` says which agent profile a key acts as; a bound key reads only the
profile's memory types everywhere. Pass `agentName` in the client options to label an
unbound key's runs and checks.
