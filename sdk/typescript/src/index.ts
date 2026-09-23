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
 * const { recommendations } = await memory.recommendations("cus_123");
 * ```
 */

import { HttpClient } from "./client.js";
import { Admin } from "./resources/admin.js";
import { Agent } from "./resources/agent.js";
import { Customers } from "./resources/customers.js";
import { Events } from "./resources/events.js";
import { Foresight } from "./resources/foresight.js";
import { MemoryResource } from "./resources/memory.js";
import type {
  ClientOptions,
  ContextResult,
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

  constructor(options: ClientOptions) {
    const http = new HttpClient(options);
    this.events = new Events(http);
    this.customers = new Customers(http);
    this.memories = new MemoryResource(http);
    this.admin = new Admin(http);
    this.foresight = new Foresight(http);
    this.agent = new Agent(http);
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
  }): Promise<QueryResult> {
    return this.memories.query(input);
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
  }): Promise<ContextResult> {
    return this.memories.context(input);
  }
}

export { MemoryApiError, MemoryConfigError, MemoryTimeoutError } from "./errors.js";
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
export * from "./types.js";
export default MemoryClient;
