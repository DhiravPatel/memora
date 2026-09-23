/** Predictive signals, next-best-action recommendations and tracked goals. */

import type { HttpClient } from "../client.js";
import type {
  Goal,
  GoalStatus,
  GoalSummary,
  Page,
  Recommendation,
  Recommendations,
  Signal,
  SignalReport,
} from "../types.js";

function toSignal(raw: any): Signal {
  return {
    key: raw.key,
    label: raw.label,
    direction: raw.direction,
    strength: raw.strength,
    horizonDays: raw.horizon_days,
    rationale: raw.rationale,
    memoryIds: raw.memory_ids ?? [],
    observed: raw.observed ?? 0,
  };
}

export function toSignalReport(raw: any): SignalReport {
  return {
    customerId: raw.customer_id,
    externalId: raw.external_id,
    name: raw.name ?? null,
    trajectory: raw.trajectory,
    churnRisk: raw.churn_risk,
    expansionScore: raw.expansion_score,
    confidence: raw.confidence,
    headline: raw.headline,
    healthScore: raw.health_score,
    signals: (raw.signals ?? []).map(toSignal),
    measurements: raw.measurements ?? {},
    series: (raw.series ?? []).map((point: any) => ({
      capturedOn: point.captured_on,
      healthScore: point.health_score,
      churnRisk: point.churn_risk,
      expansionScore: point.expansion_score,
      trajectory: point.trajectory,
    })),
    computedAt: raw.computed_at,
  };
}

function toRecommendation(raw: any): Recommendation {
  return {
    key: raw.key,
    action: raw.action,
    rationale: raw.rationale,
    category: raw.category,
    urgency: raw.urgency,
    priority: raw.priority,
    memoryIds: raw.memory_ids ?? [],
    goalIds: raw.goal_ids ?? [],
    signals: raw.signals ?? [],
    playbook: raw.playbook ?? [],
  };
}

export function toGoal(raw: any): Goal {
  return {
    id: raw.id,
    customerId: raw.customer_id,
    statement: raw.statement,
    status: raw.status,
    progress: raw.progress,
    confidence: raw.confidence,
    keywords: raw.keywords ?? [],
    memoryId: raw.memory_id ?? null,
    evidence: (raw.evidence ?? []).map((entry: any) => ({
      kind: entry.kind,
      at: entry.at ?? null,
      memoryId: entry.memory_id ?? null,
      eventId: entry.event_id ?? null,
      match: entry.match ?? null,
      cue: entry.cue ?? null,
      note: entry.note ?? null,
    })),
    openedAt: raw.opened_at,
    lastSignalAt: raw.last_signal_at,
    closedAt: raw.closed_at ?? null,
    closedReason: raw.closed_reason ?? null,
    overridden: Boolean(raw.overridden),
  };
}

export class Foresight {
  constructor(private readonly http: HttpClient) {}

  /**
   * Where a customer is heading, and the observations that say so.
   *
   * Pass `series: false` to skip the stored daily history when you only need the current
   * reading.
   */
  async signals(customerId: string, options: { series?: boolean } = {}): Promise<SignalReport> {
    return toSignalReport(
      await this.http.request<any>({
        method: "GET",
        path: `/v1/customers/${encodeURIComponent(customerId)}/signals`,
        query: { series: options.series },
      }),
    );
  }

  /** What to do about this customer next, most urgent first, with the evidence. */
  async recommendations(customerId: string): Promise<Recommendations> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/recommendations`,
    });
    return {
      customerId: raw.customer_id,
      externalId: raw.external_id,
      summary: raw.summary,
      trajectory: raw.trajectory,
      churnRisk: raw.churn_risk,
      recommendations: (raw.recommendations ?? []).map(toRecommendation),
      computedAt: raw.computed_at,
    };
  }

  /** What a customer said they were trying to do, and how far it got. */
  async goals(
    customerId: string,
    params: { status?: GoalStatus; limit?: number; offset?: number } = {},
  ): Promise<Page<Goal>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/customers/${encodeURIComponent(customerId)}/goals`,
      query: { status: params.status, limit: params.limit, offset: params.offset },
    });
    return { ...raw, data: raw.data.map(toGoal) };
  }

  /** Every tracked goal in the project, live ones first. */
  async allGoals(
    params: { status?: GoalStatus; limit?: number; offset?: number } = {},
  ): Promise<Page<Goal>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/goals",
      query: { status: params.status, limit: params.limit, offset: params.offset },
    });
    return { ...raw, data: raw.data.map(toGoal) };
  }

  async goalSummary(): Promise<GoalSummary> {
    const raw = await this.http.request<any>({ method: "GET", path: "/v1/goals/summary" });
    return {
      total: raw.total,
      open: raw.open,
      progressing: raw.progressing,
      achieved: raw.achieved,
      stalled: raw.stalled,
      abandoned: raw.abandoned,
      summary: raw.summary,
    };
  }

  /**
   * Set a goal's status by hand.
   *
   * The tracker stops touching an overridden goal, so a correction sticks rather than
   * being undone by the next event. Requires a key with `memory:write`.
   */
  async setGoalStatus(goalId: string, status: GoalStatus, note?: string): Promise<Goal> {
    return toGoal(
      await this.http.request<any>({
        method: "PATCH",
        path: `/v1/goals/${encodeURIComponent(goalId)}`,
        body: { status, note },
      }),
    );
  }
}
