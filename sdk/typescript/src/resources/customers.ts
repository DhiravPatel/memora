/** Customer profiles, memories and timelines. */

import type { HttpClient } from "../client.js";
import type {
  BriefCaution,
  Customer,
  Customer360,
  CustomerBrief,
  Memory,
  Page,
  TimelineEntry,
} from "../types.js";
import { toRecommendation, toSignal } from "./foresight.js";
import { toChange } from "./state.js";

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

function toCaution(raw: any): BriefCaution {
  return {
    text: raw.text,
    action: raw.action,
    actions: raw.actions ?? [raw.action],
    decision: raw.decision,
    summary: raw.summary,
    rules: raw.rules ?? [],
    evidence: raw.evidence ?? [],
  };
}

export function toBrief(raw: any): CustomerBrief {
  const situation = raw.situation ?? {};
  const health = situation.health ?? {};
  const plan = situation.plan ?? {};
  const preferences = raw.preferences ?? {};
  const changes = raw.recent_changes ?? {};
  const conversation = raw.last_conversation;
  return {
    customer: {
      id: raw.customer.id,
      externalId: raw.customer.external_id,
      name: raw.customer.name ?? null,
      email: raw.customer.email ?? null,
      customerSince: raw.customer.customer_since ?? null,
      lastActiveAt: raw.customer.last_active_at ?? null,
    },
    headline: raw.headline,
    situation: {
      health: {
        score: health.score,
        band: health.band,
        churnRisk: health.churn_risk,
        trajectory: health.trajectory,
        explanation: health.explanation ?? null,
      },
      plan: {
        name: plan.name ?? null,
        statement: plan.statement ?? null,
        changedAt: plan.changed_at ?? null,
        direction: plan.direction ?? null,
      },
      lifecycle: (situation.lifecycle ?? []).map((track: any) => ({
        track: track.track,
        label: track.label,
        state: track.state,
        enteredAt: track.entered_at,
        pinned: Boolean(track.pinned),
        reasons: track.reasons ?? [],
      })),
      openProblems: situation.open_problems ?? 0,
      goals: situation.goals ?? {},
    },
    talkingPoints: raw.talking_points ?? [],
    cautions: (raw.cautions ?? []).map(toCaution),
    openIssues: (raw.open_issues ?? []).map((issue: any) => ({
      id: issue.id,
      content: issue.content,
      firstSeenAt: issue.first_seen_at ?? null,
      ageDays: issue.age_days ?? null,
      timesReported: issue.times_reported ?? 1,
    })),
    goals: (raw.goals ?? []).map((goal: any) => ({
      id: goal.id,
      statement: goal.statement,
      status: goal.status,
      progress: goal.progress ?? null,
      lastSignalAt: goal.last_signal_at ?? null,
    })),
    preferences: {
      channel: preferences.channel ?? null,
      optOuts: preferences.opt_outs ?? [],
      statements: preferences.statements ?? [],
    },
    intents: (raw.intents ?? []).map((intent: any) => ({
      id: intent.id,
      type: intent.type,
      content: intent.content,
      kinds: intent.kinds ?? [],
      lastSeenAt: intent.last_seen_at ?? null,
    })),
    risks: (raw.risks ?? []).map(toSignal),
    opportunities: (raw.opportunities ?? []).map(toSignal),
    recentChanges: {
      window: {
        since: changes.window?.since,
        until: changes.window?.until,
        basis: changes.window?.basis,
        value: changes.window?.value ?? null,
        found: changes.window?.found ?? true,
        note: changes.window?.note ?? null,
        label: changes.window?.label,
      },
      summary: changes.summary ?? "",
      items: (changes.items ?? []).map(toChange),
      total: changes.total ?? 0,
      withheld: changes.withheld ?? 0,
    },
    lastConversation: conversation
      ? {
          id: conversation.id,
          agent: conversation.agent ?? null,
          summary: conversation.summary,
          closedAt: conversation.closed_at ?? null,
          turnCount: conversation.turn_count ?? null,
        }
      : null,
    nextStep: raw.next_step ? toRecommendation(raw.next_step) : null,
    setAside: raw.set_aside ?? [],
    evidence: raw.evidence ?? [],
    withheld: raw.withheld ?? 0,
    withheldFacts: raw.withheld_facts ?? [],
    generatedAt: raw.generated_at,
    markdown: raw.markdown,
  };
}

const iso = (value: Date | string | undefined) =>
  value instanceof Date ? value.toISOString() : value;

export class Customers {
  constructor(private readonly http: HttpClient) {}

  /** A decision-ready brief: the situation, what to raise and in what order, what not to
   * do and why, open issues, goals and preferences, what changed since the last
   * conversation (`since`, default `"last_session"`), and the next step.
   *
   * Cautions are the guardrails' own verdicts for this key's agent profile, so an agent
   * that follows the brief is not then refused.
   *
   * ```ts
   * const brief = await memora.customers.brief("cus_1");
   * console.log(brief.headline);
   * if (brief.cautions.some((c) => c.actions.includes("offer_upgrade"))) skipUpsell();
   * ```
   */
  async brief(
    customerId: string,
    options: { since?: Date | string; agent?: string } = {},
  ): Promise<CustomerBrief> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/brief`,
      query: { since: iso(options.since) ?? "last_session", agent: options.agent },
    });
    return toBrief(raw);
  }

  /** The brief as a Markdown page — ready for a system prompt or a ticket. */
  briefMarkdown(
    customerId: string,
    options: { since?: Date | string; agent?: string } = {},
  ): Promise<string> {
    return this.http.request<string>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/brief`,
      query: {
        since: iso(options.since) ?? "last_session",
        agent: options.agent,
        format: "markdown",
      },
    });
  }

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
  async get360(customerId: string, options: { include?: string[] } = {}): Promise<Customer360> {
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

  async list(
    params: { search?: string; limit?: number; offset?: number } = {},
  ): Promise<Page<Customer>> {
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
