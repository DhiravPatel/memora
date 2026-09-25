/** Facts, conditions, the lifecycle and snapshots (§26 phase 1). */

import type { HttpClient } from "../client.js";
import type {
  Change,
  ConditionResult,
  CustomerAt,
  CustomerChanges,
  CustomerComparison,
  LifecycleState,
} from "../types.js";

interface RawState {
  track?: string;
  reasons?: string[];
  state: string;
  previous_state: string | null;
  entered_at: string;
  source: string;
  transition: string | null;
  reason: string | null;
  evidence: string[];
  pinned: boolean;
  pinned_until: string | null;
}

function toState(raw: RawState): LifecycleState {
  return {
    track: raw.track ?? "lifecycle",
    reasons: raw.reasons ?? [],
    state: raw.state,
    previousState: raw.previous_state,
    enteredAt: raw.entered_at,
    source: raw.source,
    transition: raw.transition,
    reason: raw.reason,
    evidence: raw.evidence ?? [],
    pinned: raw.pinned,
    pinnedUntil: raw.pinned_until,
  };
}

const path = (customerId: string) => `/v1/customers/${encodeURIComponent(customerId)}`;

export function toChange(raw: any): Change {
  return {
    type: raw.type,
    kind: raw.kind,
    title: raw.title,
    before: raw.before ?? null,
    after: raw.after ?? null,
    detectedAt: raw.detected_at,
    evidence: raw.evidence ?? [],
    source: raw.source,
    track: raw.track ?? null,
    reasons: raw.reasons ?? [],
    detail: raw.detail ?? {},
    importance: raw.importance ?? 0,
    topics: raw.topics ?? [],
  };
}

function toAt(raw: any): CustomerAt {
  return {
    at: raw.at,
    live: raw.live,
    snapshotId: raw.snapshot_id ?? null,
    takenAt: raw.taken_at ?? null,
    state: raw.state ?? null,
    description: raw.description ?? null,
  };
}

const iso = (value: Date | string | undefined) => (value instanceof Date ? value.toISOString() : value);


export class State {
  constructor(private readonly http: HttpClient) {}

  /** Every fact a rule can read about a customer, with the evidence behind each. */
  facts(
    customerId: string,
  ): Promise<{
    values: Record<string, unknown>;
    evidence: Record<string, string[]>;
    withheld_facts: string[];
  }> {
    return this.http.request({ method: "GET", path: `${path(customerId)}/facts` });
  }

  catalog(): Promise<{ facts: unknown[]; metadata_prefix: string; examples: string[] }> {
    return this.http.request({ method: "GET", path: "/v1/conditions/catalog" });
  }

  /** Check a condition without evaluating it. */
  validate(
    condition: string | Record<string, unknown>,
  ): Promise<{
    valid: boolean;
    text: string | null;
    error: string | null;
    position: number | null;
  }> {
    return this.http.request({
      method: "POST",
      path: "/v1/conditions/validate",
      body: { condition },
    });
  }

  /** Evaluate a condition against a customer. Act on `matched` — unknown counts as false.
   *
   * ```ts
   * const { matched, explanation } = await memora.state.evaluate("cus_1", 'problems.entities contains "billing"');
   * ```
   */
  async evaluate(
    customerId: string,
    condition: string | Record<string, unknown>,
  ): Promise<ConditionResult> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: "/v1/conditions/evaluate",
      body: { customer_id: customerId, condition },
    });
    return {
      condition: raw.condition,
      outcome: raw.evaluation.outcome,
      matched: raw.evaluation.matched,
      explanation: raw.evaluation.explanation,
      evidence: raw.evaluation.evidence ?? [],
      leaves: raw.evaluation.leaves ?? [],
      withheldFacts: raw.withheld_facts ?? [],
    };
  }

  /** The customer's state on a track (the primary lifecycle by default), or null if they
   *  have not been placed yet. */
  async current(customerId: string, track = "lifecycle"): Promise<LifecycleState | null> {
    return (await this.tracks(customerId))[track] ?? null;
  }

  /** The customer's current state on every track, keyed by track name. */
  async tracks(customerId: string): Promise<Record<string, LifecycleState | null>> {
    const raw = await this.http.request<{
      current: RawState | null;
      tracks?: { track: string; current: RawState | null }[];
    }>({ method: "GET", path: `${path(customerId)}/state` });
    const tracks = raw.tracks?.length ? raw.tracks : [{ track: "lifecycle", current: raw.current }];
    return Object.fromEntries(
      tracks.map((item) => [item.track, item.current ? toState(item.current) : null]),
    );
  }

  /** `track: "all"` interleaves every track by time. */
  async history(
    customerId: string,
    options: { track?: string; limit?: number } = {},
  ): Promise<LifecycleState[]> {
    const raw = await this.http.request<{ data: RawState[] }>({
      method: "GET",
      path: `${path(customerId)}/state/history`,
      query: { limit: options.limit ?? 50, track: options.track ?? "lifecycle" },
    });
    return raw.data.map(toState);
  }

  /** Set the state on a track by hand. Pinned by default, so the machine leaves it alone
   *  until released. */
  async set(
    customerId: string,
    state: string,
    options: { track?: string; pin?: boolean; pinDays?: number; note?: string } = {},
  ): Promise<LifecycleState> {
    const raw = await this.http.request<RawState>({
      method: "PUT",
      path: `${path(customerId)}/state`,
      body: {
        state,
        track: options.track ?? "lifecycle",
        pin: options.pin ?? true,
        pin_days: options.pinDays ?? null,
        note: options.note ?? null,
      },
    });
    return toState(raw);
  }

  async release(customerId: string, track = "lifecycle"): Promise<LifecycleState> {
    return toState(
      await this.http.request<RawState>({
        method: "DELETE",
        path: `${path(customerId)}/state/pin`,
        query: { track },
      }),
    );
  }

  /** The shipped tracks (engagement, commercial), ready to add to `lifecycle_tracks`. */
  templates(): Promise<{ name: string; label: string; states: string[]; initial: string }[]> {
    return this.http.request({ method: "GET", path: "/v1/lifecycle/templates" });
  }

  refresh(
    customerId: string,
  ): Promise<{ state: string | null; moved: boolean; transitions: unknown[] }> {
    return this.http.request({ method: "POST", path: `${path(customerId)}/state/refresh` });
  }

  /** The project's machines — the primary lifecycle and every track — with how many
   *  customers are in each state. */
  lifecycle(): Promise<{
    enabled: boolean;
    states: string[];
    counts: Record<string, number>;
    tracks: { track: string; label: string; states: string[]; counts: Record<string, number> }[];
  }> {
    return this.http.request({ method: "GET", path: "/v1/lifecycle" });
  }

  /** Every material change in what was known about a customer, newest first. */
  async snapshots(
    customerId: string,
    options: { since?: string; until?: string; limit?: number } = {},
  ): Promise<unknown[]> {
    const raw = await this.http.request<{ data: unknown[] }>({
      method: "GET",
      path: `${path(customerId)}/snapshots`,
      query: { since: options.since, until: options.until, limit: options.limit ?? 50 },
    });
    return raw.data;
  }

  /** What changed about a customer since a moment, and what they looked like then and now.
   *
   * `since` is a span ("7d", "12h", "2w", "3mo"), a time, a snapshot id, or
   * `"last_session"` — since the last conversation — or `"last_run"`.
   *
   * ```ts
   * const { summary, changes } = await memora.state.changes("cus_1", { since: "last_session", agent: "support-bot" });
   * ```
   */
  async changes(
    customerId: string,
    options: {
      since?: Date | string;
      until?: Date | string;
      agent?: string;
      types?: string[];
      order?: "time" | "importance";
      limit?: number;
    } = {},
  ): Promise<CustomerChanges> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `${path(customerId)}/changes`,
      query: {
        since: iso(options.since),
        until: iso(options.until),
        agent: options.agent,
        types: options.types?.join(","),
        order: options.order ?? "time",
        limit: options.limit ?? 50,
      },
    });
    return {
      customerId: raw.customer_id,
      summary: raw.summary,
      changes: (raw.changes ?? []).map(toChange),
      window: {
        since: raw.window.since,
        until: raw.window.until,
        basis: raw.window.basis,
        value: raw.window.value ?? null,
        found: raw.window.found,
        note: raw.window.note ?? null,
        label: raw.window.label,
      },
      counts: raw.counts ?? {},
      total: raw.total,
      truncated: raw.truncated,
      withheld: raw.withheld ?? 0,
      then: toAt(raw.then),
      now: toAt(raw.now),
    };
  }

  /** The customer at two moments side by side ("then vs now"), with every fact that differs. */
  async compare(customerId: string, from: Date | string, to?: Date | string): Promise<CustomerComparison> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `${path(customerId)}/compare`,
      query: { from: iso(from), to: iso(to) },
    });
    return {
      customerId: raw.customer_id,
      then: toAt(raw.then),
      now: toAt(raw.now),
      differences: raw.differences ?? [],
      summary: raw.summary,
    };
  }

  /** What was known about a customer at a moment in the past. */
  snapshotAt(customerId: string, time: Date | string): Promise<unknown> {
    return this.http.request({
      method: "GET",
      path: `${path(customerId)}/snapshots/at`,
      query: { time: time instanceof Date ? time.toISOString() : time },
    });
  }
}
