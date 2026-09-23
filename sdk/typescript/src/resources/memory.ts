/** Querying memory and building agent context. */

import type { HttpClient } from "../client.js";
import type { ContextResult, QueriedMemory, QueryResult } from "../types.js";

function toMemory(raw: any): QueriedMemory {
  return {
    id: raw.id,
    type: raw.type,
    content: raw.content,
    importance: raw.importance,
    confidence: raw.confidence,
    score: raw.score,
    retrievedBy: raw.retrieved_by ?? [],
    sourceEventIds: raw.source_event_ids ?? [],
  };
}

export class MemoryResource {
  constructor(private readonly http: HttpClient) {}

  /** Ask a natural-language question about a customer. */
  async query(input: {
    customerId: string;
    query: string;
    limit?: number;
    includeTrace?: boolean;
  }): Promise<QueryResult> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/memory/query",
      body: {
        customer_id: input.customerId,
        query: input.query,
        limit: input.limit,
        include_trace: input.includeTrace ?? false,
      },
    });
    return {
      answer: raw.answer,
      confidence: raw.confidence,
      memories: (raw.memories ?? []).map(toMemory),
      sources: (raw.sources ?? []).map((source: any) => ({ eventId: source.event_id })),
      trace: raw.trace ?? undefined,
    };
  }

  /** Hybrid retrieval without an LLM answer. */
  async search(input: {
    query: string;
    customerId?: string;
    limit?: number;
    types?: string[];
  }): Promise<QueriedMemory[]> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/memory/search",
      body: {
        query: input.query,
        customer_id: input.customerId,
        limit: input.limit,
        types: input.types,
      },
    });
    return (raw.memories ?? []).map(toMemory);
  }

  /** Everything an agent should know before it answers. */
  async context(input: {
    customerId: string;
    task?: string;
    query?: string;
    limit?: number;
    tokenBudget?: number;
    format?: "json" | "text";
  }): Promise<ContextResult> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/memory/context",
      body: {
        customer_id: input.customerId,
        task: input.task,
        query: input.query,
        limit: input.limit,
        token_budget: input.tokenBudget,
        format: input.format ?? "json",
      },
    });
    const context = raw.customer_context ?? {};
    return {
      customerContext: {
        customer: context.customer ?? {},
        importantFacts: context.important_facts ?? [],
        activeProblems: context.active_problems ?? [],
        preferences: context.preferences ?? [],
        goals: context.goals ?? [],
        recentEvents: context.recent_events ?? [],
        relationships: context.relationships ?? [],
        memories: context.memories ?? [],
      },
      promptText: raw.prompt_text ?? null,
      tokenCount: raw.token_count ?? 0,
      truncated: raw.truncated ?? false,
    };
  }

  /** Record a memory directly, bypassing extraction (for known, verified facts). */
  async remember(input: {
    customerId: string;
    content: string;
    type?: string;
    importance?: number;
    confidence?: number;
  }): Promise<{ id: string; content: string }> {
    return this.http.request({
      method: "POST",
      path: "/v1/memories",
      body: {
        customer_id: input.customerId,
        content: input.content,
        type: input.type ?? "fact",
        importance: input.importance,
        confidence: input.confidence,
      },
    });
  }

  async get(memoryId: string): Promise<unknown> {
    return this.http.request({ method: "GET", path: `/v1/memories/${memoryId}` });
  }

  async delete(memoryId: string): Promise<{ deleted: boolean }> {
    return this.http.request({ method: "DELETE", path: `/v1/memories/${memoryId}` });
  }
}
