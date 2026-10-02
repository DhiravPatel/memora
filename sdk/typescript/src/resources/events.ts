/** Event ingestion. */

import type { HttpClient } from "../client.js";
import type {
  ContractCheck,
  ContractViolation,
  EventExplanation,
  RejectedEvent,
  TrackBatchResult,
  TrackEventInput,
  TrackEventResult,
} from "../types.js";

interface RawTrackResult {
  event_id: string;
  status: "accepted" | "duplicate";
  customer_id: string;
  importance: number;
  queued: boolean;
  contract?: any;
}

export function toViolation(raw: any): ContractViolation {
  return {
    path: raw.path,
    rule: raw.rule,
    expected: raw.expected ?? null,
    received: raw.received ?? null,
    message: raw.message ?? "",
  };
}

/** A memory contract check (§26 7.1), or null when no contract covers the type. */
export function toContractCheck(raw: any): ContractCheck | null {
  if (!raw) return null;
  return {
    mode: raw.mode,
    valid: Boolean(raw.valid),
    violations: (raw.violations ?? []).map(toViolation),
    version: raw.version ?? null,
    wouldRefuse: Boolean(raw.would_refuse),
    text: raw.text ?? null,
    ...(raw.definition ? { definition: raw.definition } : {}),
  };
}

function toRejected(raw: any): RejectedEvent {
  return {
    index: raw.index,
    eventType: raw.event_type,
    customerId: raw.customer_id,
    externalEventId: raw.external_event_id ?? null,
    contractVersion: raw.contract_version,
    violations: (raw.violations ?? []).map(toViolation),
  };
}

function toPayload(input: TrackEventInput): Record<string, unknown> {
  return {
    customer_id: input.customerId,
    event_type: input.type,
    data: input.data ?? {},
    external_event_id: input.externalEventId,
    occurred_at:
      input.occurredAt instanceof Date ? input.occurredAt.toISOString() : input.occurredAt,
    customer_email: input.customerEmail,
    customer_name: input.customerName,
    source: input.source ?? "sdk",
  };
}

interface RawPlan {
  content: string;
  type: string;
  action: string;
  reason: string;
  importance: number;
  confidence: number;
  similarity: number;
  rule: string | null;
  memory_id: string | null;
  closest_memory_id: string | null;
  closest_content: string | null;
  sensitivity: string;
  restricted_by: string | null;
  extracted_by: string | null;
}

interface RawExplanation {
  would_process: boolean;
  stop_reason: string | null;
  summary: string;
  importance: number;
  threshold: number;
  text: string | null;
  text_length: number;
  redacted: boolean;
  redactions: { kind: string; count: number }[];
  memories: RawPlan[];
  memory_count: number;
  entities: { name: string; type: string; status: string; entity_id: string | null }[];
  entity_count: number;
  duration_ms: number;
  contract?: any;
}

function fromExplanation(raw: RawExplanation): EventExplanation {
  return {
    wouldProcess: raw.would_process,
    stopReason: raw.stop_reason,
    summary: raw.summary,
    importance: raw.importance,
    threshold: raw.threshold,
    text: raw.text,
    textLength: raw.text_length,
    redacted: raw.redacted,
    redactions: raw.redactions ?? [],
    memories: (raw.memories ?? []).map((plan) => ({
      content: plan.content,
      type: plan.type,
      action: plan.action,
      reason: plan.reason,
      importance: plan.importance,
      confidence: plan.confidence,
      similarity: plan.similarity,
      rule: plan.rule,
      memoryId: plan.memory_id,
      closestMemoryId: plan.closest_memory_id,
      closestContent: plan.closest_content,
      sensitivity: plan.sensitivity,
      restrictedBy: plan.restricted_by,
      extractedBy: plan.extracted_by,
    })),
    memoryCount: raw.memory_count,
    entities: (raw.entities ?? []).map((entity) => ({
      name: entity.name,
      type: entity.type,
      status: entity.status,
      entityId: entity.entity_id,
    })),
    entityCount: raw.entity_count,
    durationMs: raw.duration_ms,
    contract: toContractCheck(raw.contract),
  };
}

function fromResult(raw: RawTrackResult): TrackEventResult {
  return {
    eventId: raw.event_id,
    status: raw.status,
    customerId: raw.customer_id,
    importance: raw.importance,
    queued: raw.queued,
    contract: toContractCheck(raw.contract),
  };
}

export class Events {
  constructor(private readonly http: HttpClient) {}

  /** Send a single event. Returns as soon as the event is durably stored. */
  async track(input: TrackEventInput): Promise<TrackEventResult> {
    const raw = await this.http.request<RawTrackResult>({
      method: "POST",
      path: "/v1/events",
      body: toPayload(input),
      idempotencyKey: input.externalEventId,
    });
    return fromResult(raw);
  }

  /** What this event *would* do, without sending it.
   *
   * The real pipeline, stopped before it writes: nothing is stored and no memory changes.
   * Read `stopReason` first — when it is set, nothing else happened and it says why.
   *
   * ```ts
   * const preview = await memora.events.preview({
   *   customerId: "cus_1",
   *   type: "support_message",
   *   data: { message: "The sync keeps failing." },
   * });
   * if (!preview.wouldProcess) console.log(preview.stopReason);
   * ```
   */
  async preview(
    input: Pick<TrackEventInput, "customerId" | "type" | "data" | "occurredAt">,
  ): Promise<EventExplanation> {
    const raw = await this.http.request<RawExplanation>({
      method: "POST",
      path: "/v1/events/preview",
      body: {
        customer_id: input.customerId,
        event_type: input.type,
        data: input.data ?? {},
        occurred_at:
          input.occurredAt instanceof Date ? input.occurredAt.toISOString() : input.occurredAt,
      },
    });
    return fromExplanation(raw);
  }

  /** Send up to 500 events in one request. Returns the accepted ones, in order; events an
   * enforcing memory contract refused are in `.rejected`, each with its position. */
  async trackBatch(inputs: TrackEventInput[]): Promise<TrackBatchResult> {
    const raw = await this.http.request<{
      accepted: RawTrackResult[];
      duplicates: number;
      rejected?: any[];
    }>({
      method: "POST",
      path: "/v1/events/batch",
      body: { events: inputs.map(toPayload) },
    });
    return Object.assign(raw.accepted.map(fromResult), {
      rejected: (raw.rejected ?? []).map(toRejected),
      duplicates: raw.duplicates ?? 0,
    });
  }

  async list(params: {
    customerId?: string;
    type?: string;
    status?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ data: unknown[]; total: number }> {
    return this.http.request({
      method: "GET",
      path: "/v1/events",
      query: {
        customer_id: params.customerId,
        event_type: params.type,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
      },
    });
  }
}
