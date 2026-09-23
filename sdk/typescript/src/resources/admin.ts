/** Health, links, exports and memory feedback. */

import type { HttpClient } from "../client.js";
import type { CausalChain, CustomerHealth, MemoryFeedbackResult, MemoryLink } from "../types.js";

export class Admin {
  constructor(private readonly http: HttpClient) {}

  /** Health score, band, churn risk and the factors behind them. */
  async health(customerId: string): Promise<CustomerHealth> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/health`,
    });
    return {
      customerId: raw.customer_id,
      externalId: raw.external_id,
      name: raw.name ?? null,
      score: raw.score,
      band: raw.band,
      churnRisk: raw.churn_risk,
      explanation: raw.explanation,
      factors: (raw.factors ?? []).map((factor: any) => ({
        key: factor.key,
        label: factor.label,
        contribution: factor.contribution,
        count: factor.count,
        memoryIds: factor.memory_ids ?? [],
      })),
      computedAt: raw.computed_at,
    };
  }

  /** Inferred links between memories, plus assembled causal chains. */
  async links(
    customerId: string,
    options: { linkType?: string } = {},
  ): Promise<{ links: MemoryLink[]; chains: CausalChain[] }> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/links`,
      query: { link_type: options.linkType },
    });
    return {
      links: (raw.links ?? []).map((link: any) => ({
        id: link.id,
        linkType: link.link_type,
        confidence: link.confidence,
        rationale: link.rationale ?? null,
        direction: link.direction,
        otherMemoryId: link.other_memory_id,
        otherContent: link.other_content ?? null,
        otherType: link.other_type ?? null,
      })),
      chains: (raw.chains ?? []).map((chain: any) => ({
        outcomeMemoryId: chain.outcome_memory_id,
        outcomeContent: chain.outcome_content,
        occurredAt: chain.occurred_at,
        steps: (chain.steps ?? []).map((step: any) => ({
          memoryId: step.memory_id,
          content: step.content,
          type: step.type,
          linkType: step.link_type,
          confidence: step.confidence,
          rationale: step.rationale ?? null,
          occurredAt: step.occurred_at,
        })),
      })),
    };
  }

  /** Everything known about a customer, including memory version history. */
  async export(customerId: string): Promise<Record<string, unknown>> {
    return this.http.request({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/export`,
    });
  }

  /** Confirm, reject or correct a memory. Confidence moves; history is preserved. */
  async feedback(
    memoryId: string,
    verdict: "confirm" | "reject" | "correct",
    options: { content?: string; note?: string } = {},
  ): Promise<MemoryFeedbackResult> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: `/v1/memories/${memoryId}/feedback`,
      body: { verdict, content: options.content, note: options.note },
    });
    return {
      memoryId: raw.memory_id,
      verdict: raw.verdict,
      status: raw.status,
      confidence: raw.confidence,
      replacementMemoryId: raw.replacement_memory_id ?? null,
    };
  }

  /** Merge a duplicate customer into another. Events, memories and links all move. */
  async mergeCustomers(
    sourceCustomerId: string,
    targetCustomerId: string,
  ): Promise<{ eventsMoved: number; memoriesMoved: number; linksMoved: number }> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: `/v1/customers/${encodeURIComponent(sourceCustomerId)}/merge`,
      body: { into: targetCustomerId },
    });
    return {
      eventsMoved: raw.events_moved,
      memoriesMoved: raw.memories_moved,
      linksMoved: raw.links_moved,
    };
  }

  /** Create or update up to 500 customers in one request. */
  async upsertCustomers(
    customers: { externalId: string; email?: string; name?: string; metadata?: Record<string, unknown> }[],
  ): Promise<{ created: number; updated: number; customerIds: string[] }> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/customers/batch",
      body: {
        customers: customers.map((customer) => ({
          external_id: customer.externalId,
          email: customer.email,
          name: customer.name,
          metadata: customer.metadata,
        })),
      },
    });
    return { created: raw.created, updated: raw.updated, customerIds: raw.customer_ids ?? [] };
  }

  /** Re-queue a failed event. Replay is always safe. */
  async retryEvent(eventId: string): Promise<{ eventId: string; queued: boolean }> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: `/v1/events/${eventId}/retry`,
    });
    return { eventId: raw.event_id, queued: raw.queued };
  }
}
