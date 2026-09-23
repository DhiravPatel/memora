/**
 * Agent sessions: memory that survives between conversations.
 *
 * ```ts
 * const session = await memory.agent.open({ customerId, externalId: conversationId });
 * const reply = await llm(session.context!.text, userMessage);
 * await memory.agent.turn(session.id, { role: "user", content: userMessage });
 * await memory.agent.turn(session.id, { role: "agent", content: reply });
 * await memory.agent.close(session.id, { outcome: "Escalated to engineering" });
 * ```
 *
 * The next `open` for the same customer is handed the summary of this conversation.
 */

import type { HttpClient } from "../client.js";
import type {
  AgentSession,
  Page,
  SessionContext,
  SessionStatus,
  Turn,
  TurnResult,
} from "../types.js";

function toContext(raw: any): SessionContext | null {
  if (!raw) return null;
  return {
    text: raw.text ?? "",
    memoryIds: raw.memory_ids ?? [],
    tokenEstimate: raw.token_estimate ?? 0,
    truncated: Boolean(raw.truncated),
    priorSessions: (raw.prior_sessions ?? []).map((session: any) => ({
      id: session.id,
      agent: session.agent,
      summary: session.summary,
      turnCount: session.turn_count ?? 0,
      startedAt: session.started_at,
      closedAt: session.closed_at ?? null,
    })),
  };
}

function toTurn(raw: any): Turn {
  return {
    id: raw.id,
    role: raw.role,
    content: raw.content,
    occurredAt: raw.occurred_at,
    eventId: raw.event_id ?? null,
    retrievedMemoryIds: raw.retrieved_memory_ids ?? [],
  };
}

function toSession(raw: any): AgentSession {
  return {
    id: raw.id,
    projectId: raw.project_id,
    customerId: raw.customer_id,
    externalId: raw.external_id ?? null,
    agent: raw.agent,
    channel: raw.channel ?? null,
    status: raw.status,
    turnCount: raw.turn_count ?? 0,
    startedAt: raw.started_at,
    lastActiveAt: raw.last_active_at,
    closedAt: raw.closed_at ?? null,
    summary: raw.summary ?? null,
    summaryMemoryId: raw.summary_memory_id ?? null,
    memoryIds: raw.memory_ids ?? [],
    resumed: Boolean(raw.resumed),
    context: toContext(raw.context),
    turns: (raw.turns ?? []).map(toTurn),
  };
}

export class Agent {
  constructor(private readonly http: HttpClient) {}

  /**
   * Open a session and get briefed on the customer.
   *
   * Pass your own conversation id as `externalId` and this is idempotent: a reconnect
   * resumes the same session instead of starting a second one.
   */
  async open(input: {
    customerId: string;
    agent?: string;
    externalId?: string;
    channel?: string;
    tokenBudget?: number;
    metadata?: Record<string, unknown>;
  }): Promise<AgentSession> {
    return toSession(
      await this.http.request<any>({
        method: "POST",
        path: "/v1/agent/sessions",
        body: {
          customer_id: input.customerId,
          agent: input.agent,
          external_id: input.externalId,
          channel: input.channel,
          token_budget: input.tokenBudget,
          metadata: input.metadata,
        },
      }),
    );
  }

  /**
   * Record a turn.
   *
   * Customer turns become long-term memory by default and come back with an
   * evidence-backed answer plus fresh context. Agent turns are recorded but not learned
   * from — pass `remember: true` if you want the agent's own words remembered too.
   */
  async turn(
    sessionId: string,
    input: {
      role?: "user" | "agent" | "system";
      content: string;
      remember?: boolean;
      retrieve?: boolean;
      occurredAt?: Date | string;
      metadata?: Record<string, unknown>;
    },
  ): Promise<TurnResult> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: `/v1/agent/sessions/${encodeURIComponent(sessionId)}/turns`,
      body: {
        role: input.role ?? "user",
        content: input.content,
        remember: input.remember,
        retrieve: input.retrieve,
        occurred_at:
          input.occurredAt instanceof Date ? input.occurredAt.toISOString() : input.occurredAt,
        metadata: input.metadata,
      },
    });
    return {
      sessionId: raw.session_id,
      turn: toTurn(raw.turn),
      context: toContext(raw.context),
      answer: raw.answer ?? null,
      answerConfidence: raw.answer_confidence ?? null,
      eventId: raw.event_id ?? null,
      turnCount: raw.turn_count ?? 0,
    };
  }

  /** Close the session, writing what it established into long-term memory. */
  async close(
    sessionId: string,
    input: { writeSummary?: boolean; outcome?: string } = {},
  ): Promise<{ session: AgentSession; summary: string | null; summaryMemoryId: string | null }> {
    const raw = await this.http.request<any>({
      method: "POST",
      path: `/v1/agent/sessions/${encodeURIComponent(sessionId)}/close`,
      body: { write_summary: input.writeSummary ?? true, outcome: input.outcome },
    });
    return {
      session: toSession(raw.session),
      summary: raw.summary ?? null,
      summaryMemoryId: raw.summary_memory_id ?? null,
    };
  }

  /** A session with its full transcript. */
  async get(sessionId: string): Promise<AgentSession> {
    return toSession(
      await this.http.request<any>({
        method: "GET",
        path: `/v1/agent/sessions/${encodeURIComponent(sessionId)}`,
      }),
    );
  }

  async list(
    params: {
      customerId?: string;
      status?: SessionStatus;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<Page<AgentSession>> {
    const raw = await this.http.request<any>({
      method: "GET",
      path: "/v1/agent/sessions",
      query: {
        customer_id: params.customerId,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
      },
    });
    return { ...raw, data: raw.data.map(toSession) };
  }
}
