/**
 * Guardrails, approvals, runs and agent profiles (§26 phase 3).
 *
 * ```ts
 * const check = await memory.guardrails.check({ customerId, action: "offer_discount", request: { amount: 20 } });
 * if (check.decision === "require_approval") {
 *   const approval = await memory.guardrails.waitForApproval(check.approval!.id, { timeoutMs: 60_000 });
 *   if (approval.status === "approved") {
 *     await memory.guardrails.check({ customerId, action: "offer_discount", request: { amount: 20 }, approvalId: approval.id });
 *   }
 * }
 * ```
 */

import type { HttpClient } from "../client.js";
import type {
  ActionCheck,
  AgentProfile,
  AgentRun,
  Approval,
  ApprovalStatus,
  Decision,
  GuardrailReason,
  Page,
  RunExplanation,
  RunMemory,
} from "../types.js";

function toReason(raw: any): GuardrailReason {
  return {
    rule: raw.rule,
    source: raw.source,
    decision: raw.decision,
    explanation: raw.explanation,
    evidence: raw.evidence ?? [],
    evaluation: raw.evaluation ?? null,
  };
}

export function toApproval(raw: any): Approval {
  return {
    id: raw.id,
    customerId: raw.customer_id,
    checkId: raw.check_id,
    agent: raw.agent ?? null,
    action: raw.action,
    request: raw.request ?? {},
    reasons: (raw.reasons ?? []).map(toReason),
    status: raw.status,
    note: raw.note ?? null,
    decidedBy: raw.decided_by ?? null,
    decidedAt: raw.decided_at ?? null,
    usedAt: raw.used_at ?? null,
    expiresAt: raw.expires_at,
    createdAt: raw.created_at,
  };
}

export function toCheck(raw: any): ActionCheck {
  return {
    id: raw.id,
    customerId: raw.customer_id,
    action: raw.action,
    decision: raw.decision,
    allowed: Boolean(raw.allowed),
    summary: raw.summary ?? "",
    reasons: (raw.reasons ?? []).map(toReason),
    evidence: raw.evidence ?? [],
    approval: raw.approval ? toApproval(raw.approval) : null,
    agent: raw.agent ?? null,
    profile: raw.profile ?? null,
    request: raw.request ?? {},
    checkedAt: raw.checked_at,
  };
}

function toRun(raw: any): AgentRun {
  return {
    id: raw.id,
    kind: raw.kind,
    customerId: raw.customer_id ?? null,
    query: raw.query,
    answer: raw.answer ?? null,
    agent: raw.agent ?? null,
    sessionId: raw.session_id ?? null,
    snapshotId: raw.snapshot_id ?? null,
    memoryCount: raw.memory_count ?? 0,
    citedCount: raw.cited_count ?? 0,
    withheld: raw.withheld ?? 0,
    createdAt: raw.created_at,
    memoryIds: raw.memory_ids,
    trace: raw.trace,
  };
}

function toRunMemory(raw: any): RunMemory {
  return {
    id: raw.id,
    rank: raw.rank,
    type: raw.type ?? null,
    score: raw.score ?? 0,
    strategies: raw.strategies ?? [],
    cited: Boolean(raw.cited),
    scores: raw.scores ?? {},
    visible: raw.visible ?? true,
    contentThen: raw.content_then ?? null,
    contentNow: raw.content_now ?? null,
    statusNow: raw.status_now ?? null,
    changedSince: raw.changed_since ?? [],
  };
}

function toProfile(raw: any): AgentProfile {
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description ?? null,
    readableTypes: raw.readable_types ?? [],
    canReadRestricted: Boolean(raw.can_read_restricted),
    allowedActions: raw.allowed_actions ?? [],
    deniedActions: raw.denied_actions ?? [],
    keys: raw.keys ?? 0,
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class Guardrails {
  constructor(private readonly http: HttpClient) {}

  /**
   * Ask before acting: may this agent do `action` to this customer, now?
   *
   * `request` carries what your rules read — `channel`, `amount`, `topic`, `plan`, or
   * anything else. A `require_approval` result files a request for a person
   * (`check.approval`); once approved, check again with `approvalId` to redeem it — once.
   */
  async check(input: {
    customerId: string;
    action: string;
    request?: Record<string, unknown>;
    approvalId?: string;
    sessionId?: string;
    agent?: string;
    dryRun?: boolean;
  }): Promise<ActionCheck> {
    return toCheck(
      await this.http.request<any>({
        method: "POST",
        path: "/v1/agent/check",
        body: {
          customer_id: input.customerId,
          action: input.action,
          request: input.request ?? {},
          approval_id: input.approvalId,
          session_id: input.sessionId,
          agent: input.agent,
          dry_run: input.dryRun || undefined,
        },
      }),
    );
  }

  async checks(
    params: {
      customerId?: string;
      decision?: Decision;
      agent?: string;
      sessionId?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<Page<ActionCheck>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/agent/checks",
      query: {
        customer_id: params.customerId,
        decision: params.decision,
        agent: params.agent,
        session_id: params.sessionId,
        limit: params.limit,
        offset: params.offset,
      },
    });
    return { ...raw, data: raw.data.map(toCheck) };
  }

  async approval(approvalId: string): Promise<Approval> {
    return toApproval(
      await this.http.request<any>({
        method: "GET",
        path: `/v1/agent/approvals/${encodeURIComponent(approvalId)}`,
      }),
    );
  }

  async approvals(
    params: { status?: ApprovalStatus; customerId?: string; limit?: number; offset?: number } = {},
  ): Promise<Page<Approval>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/agent/approvals",
      query: {
        status: params.status,
        customer_id: params.customerId,
        limit: params.limit,
        offset: params.offset,
      },
    });
    return { ...raw, data: raw.data.map(toApproval) };
  }

  /** Approve or reject. Needs a key with `approvals:decide` — never the agent's own. */
  async decide(
    approvalId: string,
    decision: "approve" | "reject",
    note?: string,
  ): Promise<Approval> {
    return toApproval(
      await this.http.request<any>({
        method: "POST",
        path: `/v1/agent/approvals/${encodeURIComponent(approvalId)}/decision`,
        body: { decision, note },
      }),
    );
  }

  /**
   * Poll until a person decides or the request lapses. For anything longer than a
   * conversation, subscribe to the `agent.approval_decided` webhook instead.
   */
  async waitForApproval(
    approvalId: string,
    options: { timeoutMs?: number; intervalMs?: number } = {},
  ): Promise<Approval> {
    const deadline = Date.now() + (options.timeoutMs ?? 300_000);
    for (;;) {
      const current = await this.approval(approvalId);
      if (current.status !== "pending" || Date.now() >= deadline) return current;
      await sleep(Math.max(500, options.intervalMs ?? 5_000));
    }
  }

  /** The actions the built-in rules recognise, and the rules themselves. */
  catalog(): Promise<{
    actions: { action: string; family: string }[];
    builtin_rules: { rule: string; description: string }[];
  }> {
    return this.http.request({ method: "GET", path: "/v1/agent/guardrails" });
  }
}

export class Runs {
  constructor(private readonly http: HttpClient) {}

  async list(
    params: {
      customerId?: string;
      agent?: string;
      sessionId?: string;
      kind?: "query" | "context";
      since?: Date | string;
      until?: Date | string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<Page<AgentRun>> {
    const iso = (value?: Date | string) => (value instanceof Date ? value.toISOString() : value);
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/agent/runs",
      query: {
        customer_id: params.customerId,
        agent: params.agent,
        session_id: params.sessionId,
        kind: params.kind,
        since: iso(params.since),
        until: iso(params.until),
        limit: params.limit,
        offset: params.offset,
      },
    });
    return { ...raw, data: raw.data.map(toRun) };
  }

  async get(runId: string): Promise<AgentRun> {
    return toRun(
      await this.http.request<any>({
        method: "GET",
        path: `/v1/agent/runs/${encodeURIComponent(runId)}`,
      }),
    );
  }

  /** Why did the agent say that? The memories as they were then and are now, what was held
   *  back, the customer's recorded state at that moment, and the checks around it. */
  async explain(runId: string): Promise<RunExplanation> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: `/v1/agent/runs/${encodeURIComponent(runId)}/explain`,
    });
    return {
      run: toRun(raw.run),
      narrative: raw.narrative ?? [],
      memories: (raw.memories ?? []).map(toRunMemory),
      heldBack: raw.held_back ?? {},
      stateThen: raw.state_then ?? null,
      checks: (raw.checks ?? []).map(toCheck),
    };
  }
}

export class Profiles {
  constructor(private readonly http: HttpClient) {}

  /** The profile this key acts as, or null for an unbound key. */
  async me(): Promise<AgentProfile | null> {
    const raw = await this.http.request<any>({ method: "GET", path: "/v1/agent/profiles/me" });
    return raw ? toProfile(raw) : null;
  }

  async list(): Promise<AgentProfile[]> {
    const raw = await this.http.request<any[]>({ method: "GET", path: "/v1/agent/profiles" });
    return raw.map(toProfile);
  }

  /** Needs an `admin` key. */
  async create(input: {
    name: string;
    description?: string;
    readableTypes?: string[];
    canReadRestricted?: boolean;
    allowedActions?: string[];
    deniedActions?: string[];
  }): Promise<AgentProfile> {
    return toProfile(
      await this.http.request<any>({
        method: "POST",
        path: "/v1/agent/profiles",
        body: {
          name: input.name,
          description: input.description,
          readable_types: input.readableTypes ?? [],
          can_read_restricted: input.canReadRestricted ?? false,
          allowed_actions: input.allowedActions ?? [],
          denied_actions: input.deniedActions ?? [],
        },
      }),
    );
  }

  async update(
    profileId: string,
    changes: {
      description?: string;
      readableTypes?: string[];
      canReadRestricted?: boolean;
      allowedActions?: string[];
      deniedActions?: string[];
    },
  ): Promise<AgentProfile> {
    return toProfile(
      await this.http.request<any>({
        method: "PATCH",
        path: `/v1/agent/profiles/${encodeURIComponent(profileId)}`,
        body: {
          description: changes.description,
          readable_types: changes.readableTypes,
          can_read_restricted: changes.canReadRestricted,
          allowed_actions: changes.allowedActions,
          denied_actions: changes.deniedActions,
        },
      }),
    );
  }

  async delete(profileId: string): Promise<void> {
    await this.http.request({
      method: "DELETE",
      path: `/v1/agent/profiles/${encodeURIComponent(profileId)}`,
    });
  }
}
