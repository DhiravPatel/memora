/**
 * @ai-memory/sdk — persistent customer memory for AI-powered SaaS.
 *
 * ```ts
 * const memory = new MemoryClient({ apiKey: process.env.MEMORY_API_KEY! });
 *
 * await memory.events.track({
 *   customerId: "cus_123",
 *   type: "feature_used",
 *   data: { feature: "campaign_builder" },
 * });
 *
 * const context = await memory.context({ customerId: "cus_123", task: "support_response" });
 * const brief = await memory.brief("cus_123"); // what to raise, what not to do
 * const { recommendations } = await memory.recommendations("cus_123");
 * ```
 */

import { HttpClient } from "./client.js";
import { Admin } from "./resources/admin.js";
import { Agent } from "./resources/agent.js";
import { Customers } from "./resources/customers.js";
import { Events } from "./resources/events.js";
import { Foresight } from "./resources/foresight.js";
import { Actions, Guardrails, Profiles, Runs } from "./resources/guardrails.js";
import { MemoryResource } from "./resources/memory.js";
import { Quality } from "./resources/quality.js";
import { State } from "./resources/state.js";
import type {
  ClientOptions,
  ContextResult,
  CustomerBrief,
  QueryResult,
  Recommendations,
  SignalReport,
} from "./types.js";

export class MemoryClient {
  readonly events: Events;
  readonly customers: Customers;
  readonly memories: MemoryResource;
  /** Health, links, exports, feedback, merges and bulk operations. */
  readonly admin: Admin;
  /** Predictive signals, next-best-action recommendations and tracked goals. */
  readonly foresight: Foresight;
  /** Sessions that give an agent memory between conversations. */
  readonly agent: Agent;
  /** Facts, the condition language, the lifecycle and snapshots of what was known. */
  readonly state: State;
  /** The quality report and retrieval evaluation. */
  readonly quality: Quality;
  /** Ask before acting: checks, approvals. */
  readonly guardrails: Guardrails;
  /** The approval gateway: request an action, proceed once approved, report the outcome. */
  readonly actions: Actions;
  /** Every answer and briefing an agent asked for, and why it said what it said. */
  readonly runs: Runs;
  /** Agent profiles: which memory an agent may read and which actions it may take. */
  readonly profiles: Profiles;

  constructor(options: ClientOptions) {
    const http = new HttpClient(options);
    this.events = new Events(http);
    this.customers = new Customers(http);
    this.memories = new MemoryResource(http);
    this.admin = new Admin(http);
    this.foresight = new Foresight(http);
    this.agent = new Agent(http);
    this.state = new State(http);
    this.quality = new Quality(http);
    this.guardrails = new Guardrails(http);
    this.actions = new Actions(http);
    this.runs = new Runs(http);
    this.profiles = new Profiles(http);
  }

  /** Shorthand for `guardrails.check`. */
  checkAction(input: {
    customerId: string;
    action: string;
    request?: Record<string, unknown>;
    approvalId?: string;
    sessionId?: string;
    dryRun?: boolean;
  }) {
    return this.guardrails.check(input);
  }

  /** Shorthand for `admin.health`. */
  health(customerId: string) {
    return this.admin.health(customerId);
  }

  /** Shorthand for `memories.query`. */
  query(input: {
    customerId: string;
    query: string;
    limit?: number;
    includeTrace?: boolean;
    sessionId?: string;
  }): Promise<QueryResult> {
    return this.memories.query(input);
  }

  /** Shorthand for `customers.brief`: what to raise, what not to do and why. */
  brief(
    customerId: string,
    options: { since?: Date | string; agent?: string } = {},
  ): Promise<CustomerBrief> {
    return this.customers.brief(customerId, options);
  }

  /** Shorthand for `foresight.signals`. */
  signals(customerId: string): Promise<SignalReport> {
    return this.foresight.signals(customerId);
  }

  /** Shorthand for `foresight.recommendations`. */
  recommendations(customerId: string): Promise<Recommendations> {
    return this.foresight.recommendations(customerId);
  }

  /** Shorthand for `memories.context`. */
  context(input: {
    customerId: string;
    task?: string;
    query?: string;
    limit?: number;
    tokenBudget?: number;
    format?: "json" | "text";
    sessionId?: string;
  }): Promise<ContextResult> {
    return this.memories.context(input);
  }
}

export {
  ActionDeniedError,
  ApprovalRequiredError,
  MemoryApiError,
  MemoryConfigError,
  MemoryTimeoutError,
} from "./errors.js";
export { MemoryAgent } from "./agent-loop.js";
export type { MemoryAgentOptions, TurnContext } from "./agent-loop.js";
export {
  DELIVERY_HEADER,
  EVENT_HEADER,
  SIGNATURE_HEADER,
  TIMESTAMP_HEADER,
  WebhookVerificationError,
  constructWebhookEvent,
  verifyWebhookSignature,
} from "./webhooks.js";
export type { WebhookEventEnvelope } from "./webhooks.js";
export type { Expectation } from "./resources/quality.js";
export * from "./types.js";
export default MemoryClient;
