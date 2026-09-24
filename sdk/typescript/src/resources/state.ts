/** Facts, conditions, the lifecycle and snapshots (§26 phase 1). */

import type { HttpClient } from "../client.js";
import type { ConditionResult, LifecycleState } from "../types.js";

interface RawState {
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

  /** The customer's lifecycle state, or null if they have not been placed yet. */
  async current(customerId: string): Promise<LifecycleState | null> {
    const raw = await this.http.request<{ current: RawState | null }>({
      method: "GET",
      path: `${path(customerId)}/state`,
    });
    return raw.current ? toState(raw.current) : null;
  }

  async history(customerId: string, limit = 50): Promise<LifecycleState[]> {
    const raw = await this.http.request<{ data: RawState[] }>({
      method: "GET",
      path: `${path(customerId)}/state/history`,
      query: { limit },
    });
    return raw.data.map(toState);
  }

  /** Set the state by hand. Pinned by default, so the machine leaves it alone until released. */
  async set(
    customerId: string,
    state: string,
    options: { pin?: boolean; pinDays?: number; note?: string } = {},
  ): Promise<LifecycleState> {
    const raw = await this.http.request<RawState>({
      method: "PUT",
      path: `${path(customerId)}/state`,
      body: {
        state,
        pin: options.pin ?? true,
        pin_days: options.pinDays ?? null,
        note: options.note ?? null,
      },
    });
    return toState(raw);
  }

  async release(customerId: string): Promise<LifecycleState> {
    return toState(
      await this.http.request<RawState>({
        method: "DELETE",
        path: `${path(customerId)}/state/pin`,
      }),
    );
  }

  refresh(
    customerId: string,
  ): Promise<{ state: string | null; moved: boolean; transitions: unknown[] }> {
    return this.http.request({ method: "POST", path: `${path(customerId)}/state/refresh` });
  }

  /** The project's lifecycle machine and how many customers are in each state. */
  lifecycle(): Promise<{ enabled: boolean; states: string[]; counts: Record<string, number> }> {
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

  /** What was known about a customer at a moment in the past. */
  snapshotAt(customerId: string, time: Date | string): Promise<unknown> {
    return this.http.request({
      method: "GET",
      path: `${path(customerId)}/snapshots/at`,
      query: { time: time instanceof Date ? time.toISOString() : time },
    });
  }
}
