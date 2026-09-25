/** Freshness and drift (§26 5.5): how current what is known is, and what may be out of date. */

import type { HttpClient } from "../client.js";
import type { CustomerFreshness, DriftFlag, DriftRun, Freshness, Page } from "../types.js";

export function toFreshness(raw: any): Freshness | null {
  if (!raw) return null;
  return {
    state: raw.state,
    effectiveConfidence: raw.effective_confidence,
    evidenceAt: raw.evidence_at,
    daysSinceEvidence: raw.days_since_evidence,
    windowDays: raw.window_days,
    reasons: raw.reasons ?? [],
    contradictedAt: raw.contradicted_at ?? null,
    drift: raw.drift ?? [],
  };
}

export function toDrift(raw: any): DriftFlag {
  return {
    id: raw.id,
    kind: raw.kind,
    kindLabel: raw.kind_label,
    status: raw.status,
    stated: raw.stated,
    observed: raw.observed ?? null,
    summary: raw.summary,
    counts: raw.counts ?? {},
    evidence: raw.evidence ?? [],
    since: raw.since,
    detectedAt: raw.detected_at,
    resolvedAt: raw.resolved_at ?? null,
    resolvedByType: raw.resolved_by_type ?? null,
    note: raw.note ?? null,
    replacementMemoryId: raw.replacement_memory_id ?? null,
    memory: raw.memory ?? { id: "" },
    customer: {
      id: raw.customer?.id,
      externalId: raw.customer?.external_id ?? null,
      name: raw.customer?.name ?? null,
    },
  };
}

export class Drift {
  constructor(private readonly http: HttpClient) {}

  /** Drift flags, newest first — open ones by default.
   *
   * ```ts
   * const { data } = await memora.drift.list({ customerId: "cus_1" });
   * for (const flag of data) console.log(flag.kind, flag.summary);
   * ```
   */
  async list(
    options: {
      customerId?: string;
      status?: "open" | "confirmed" | "dismissed" | "cleared" | "all";
      kind?: DriftFlag["kind"];
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<Page<DriftFlag>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/drift",
      query: {
        customer_id: options.customerId,
        status: options.status ?? "open",
        kind: options.kind,
        limit: options.limit ?? 50,
        offset: options.offset ?? 0,
      },
    });
    return { ...raw, data: (raw.data ?? []).map(toDrift) };
  }

  async get(driftId: string): Promise<DriftFlag> {
    return toDrift(
      await this.http.request<any>({
        method: "GET",
        path: `/v1/drift/${encodeURIComponent(driftId)}`,
      }),
    );
  }

  /** The evidence is right: write the change it points to. The replaced memory is kept. */
  async confirm(driftId: string, options: { note?: string } = {}): Promise<DriftFlag> {
    return toDrift(
      await this.http.request<any>({
        method: "POST",
        path: `/v1/drift/${encodeURIComponent(driftId)}/confirm`,
        body: { note: options.note ?? null },
      }),
    );
  }

  /** The memory still holds: keep it, and count only evidence newer than now. */
  async dismiss(driftId: string, options: { note?: string } = {}): Promise<DriftFlag> {
    return toDrift(
      await this.http.request<any>({
        method: "POST",
        path: `/v1/drift/${encodeURIComponent(driftId)}/dismiss`,
        body: { note: options.note ?? null },
      }),
    );
  }

  /** Run every detector for a customer now, rather than at the nightly sweep. */
  async refresh(customerId: string): Promise<DriftRun> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: `/v1/customers/${encodeURIComponent(customerId)}/drift/refresh`,
    });
    return {
      opened: raw.opened,
      refreshed: raw.refreshed,
      cleared: raw.cleared,
      open: (raw.open ?? []).map(toDrift),
    };
  }

  /** How current what is known about a customer is, with what to look at first. */
  async freshness(
    customerId: string,
    options: { limit?: number } = {},
  ): Promise<CustomerFreshness> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/freshness`,
      query: { limit: options.limit ?? 50 },
    });
    return {
      customerId: raw.customer_id,
      counts: raw.counts,
      total: raw.total,
      needsAttention: raw.needs_attention,
      staleShare: raw.stale_share ?? null,
      memories: (raw.memories ?? []).map((item: any) => ({
        id: item.id,
        type: item.type,
        content: item.content,
        importance: item.importance,
        confidence: item.confidence,
        lastSeenAt: item.last_seen_at,
        freshness: toFreshness(item.freshness)!,
      })),
      drift: (raw.drift ?? []).map(toDrift),
      windows: raw.windows ?? {},
      withheld: raw.withheld ?? 0,
    };
  }
}
