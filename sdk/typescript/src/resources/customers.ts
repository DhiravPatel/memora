/** Customer profiles, memories and timelines. */

import type { HttpClient } from "../client.js";
import type { Customer, Customer360, Memory, Page, TimelineEntry } from "../types.js";

function toCustomer(raw: any): Customer {
  return {
    id: raw.id,
    externalId: raw.external_id,
    email: raw.email ?? null,
    name: raw.name ?? null,
    metadata: raw.metadata ?? {},
    lastEventAt: raw.last_event_at ?? null,
    createdAt: raw.created_at,
  };
}

function toMemory(raw: any): Memory {
  return {
    id: raw.id,
    customerId: raw.customer_id,
    type: raw.type,
    content: raw.content,
    importance: raw.importance,
    confidence: raw.confidence,
    status: raw.status,
    evidenceCount: raw.evidence_count,
    sourceEventIds: raw.source_event_ids ?? [],
    firstSeenAt: raw.first_seen_at,
    lastSeenAt: raw.last_seen_at,
    expiresAt: raw.expires_at ?? null,
  };
}

interface RawCustomer360 {
  customer: Record<string, unknown>;
  summary: string;
  sections: Record<string, any>;
  withheld: number;
  generated_at: string;
}

export class Customers {
  constructor(private readonly http: HttpClient) {}

  /** Everything worth knowing about a customer, in one call.
   *
   * What an agent reads before it replies: health, current plan, open problems, stated
   * preferences and goals, where the customer is heading, what to do about it, and what
   * was said last time.
   *
   * Pass `include` to build only some of it — the response goes into somebody's context
   * window, so the sections you do not need are worth not asking for.
   *
   * ```ts
   * const view = await memora.customers.get360("cus_1", {
   *   include: ["health", "active_problems"],
   * });
   * if (view.sections.health?.band === "critical") escalate(view.summary);
   * ```
   */
  async get360(
    customerId: string,
    options: { include?: string[] } = {},
  ): Promise<Customer360> {
    const raw = await this.http.request<RawCustomer360>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/360`,
      query: options.include?.length ? { include: options.include.join(",") } : undefined,
    });
    return {
      customer: raw.customer,
      summary: raw.summary,
      sections: raw.sections,
      withheld: raw.withheld,
      generatedAt: raw.generated_at,
    };
  }

  async upsert(input: {
    externalId: string;
    email?: string;
    name?: string;
    metadata?: Record<string, unknown>;
  }): Promise<Customer> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/customers",
      body: {
        external_id: input.externalId,
        email: input.email,
        name: input.name,
        metadata: input.metadata,
      },
    });
    return toCustomer(raw);
  }

  /** Accepts either your own customer id or the internal `cus_…` id. */
  async get(customerId: string): Promise<Customer> {
    return toCustomer(
      await this.http.request<any>({
        method: "GET",
        path: `/v1/customers/${encodeURIComponent(customerId)}`,
      }),
    );
  }

  async list(params: { search?: string; limit?: number; offset?: number } = {}): Promise<
    Page<Customer>
  > {
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/customers",
      query: { search: params.search, limit: params.limit, offset: params.offset },
    });
    return { ...raw, data: raw.data.map(toCustomer) };
  }

  async memories(
    customerId: string,
    params: { type?: string; status?: string; limit?: number; offset?: number } = {},
  ): Promise<Page<Memory>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/memories`,
      query: {
        type: params.type,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
      },
    });
    return { ...raw, data: raw.data.map(toMemory) };
  }

  async timeline(customerId: string, limit = 100): Promise<TimelineEntry[]> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/timeline`,
      query: { limit },
    });
    return (raw.entries ?? []).map((entry: any) => ({
      kind: entry.kind,
      id: entry.id,
      title: entry.title,
      detail: entry.detail ?? null,
      occurredAt: entry.occurred_at,
      metadata: entry.metadata ?? {},
    }));
  }

  async graph(customerId: string, depth = 2): Promise<{ nodes: unknown[]; edges: unknown[] }> {
    return this.http.request({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/graph`,
      query: { depth },
    });
  }

  /** Delete a customer and everything derived from them. */
  async delete(customerId: string): Promise<{ deleted: boolean; removed: Record<string, number> }> {
    return this.http.request({
      method: "DELETE",
      path: `/v1/customers/${encodeURIComponent(customerId)}`,
    });
  }
}
