/**
 * The memory loop around an agent, done for you (§26 3.6).
 *
 * Before the model replies, find out what is known about the customer; after it replies,
 * record the turn; before it acts, ask whether it may; when the conversation ends, leave a
 * summary for next time. `MemoryAgent` is that loop around whatever model or framework you
 * use — it never calls a model itself.
 *
 * ```ts
 * const agent = new MemoryAgent(memory, { customerId: "cus_123", agent: "support-bot", conversationId: ticket.id });
 * await agent.run(async () => {
 *   const reply = await agent.respond(ticket.message, (prompt, message) => llm({ system: prompt, user: message }));
 *   await agent.guard("offer_discount", { amount: 20 }); // throws ActionDeniedError / ApprovalRequiredError
 * });
 * ```
 */

import { ActionDeniedError, ApprovalRequiredError, MemoryConfigError } from "./errors.js";
import type { MemoryClient } from "./index.js";
import type { ActionCheck, AgentSession, TurnResult } from "./types.js";

export interface TurnContext {
  /** The briefing on the customer plus what is relevant to this message: a system prompt. */
  prompt: string;
  /** Memora's own evidence-backed answer to the message, when it had one. */
  answer: string | null;
  memoryIds: string[];
  sessionId: string;
}

export interface MemoryAgentOptions {
  customerId: string;
  agent?: string;
  /** Your conversation id: reconnecting with it resumes the same session. */
  conversationId?: string;
  channel?: string;
  tokenBudget?: number;
  writeSummary?: boolean;
}

type Json = string | number | boolean | null;

function plain(request: Record<string, unknown> | undefined): Record<string, Json> {
  const result: Record<string, Json> = {};
  for (const [key, value] of Object.entries(request ?? {})) {
    if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
      result[key] = value as Json;
    }
  }
  return result;
}

function briefingOf(session: AgentSession): string {
  if (!session.context) return "";
  const blocks = [session.context.text];
  for (const prior of session.context.priorSessions) {
    blocks.push(`Earlier conversation (${prior.agent}): ${prior.summary}`);
  }
  return blocks.filter(Boolean).join("\n\n");
}

export class MemoryAgent {
  session: AgentSession | null = null;

  constructor(
    private readonly memory: MemoryClient,
    private readonly options: MemoryAgentOptions,
  ) {
    if (!options.customerId) throw new MemoryConfigError("customerId is required.");
  }

  /** Open the session — or resume it, when `conversationId` was seen before. */
  async start(): Promise<AgentSession> {
    if (!this.session) {
      this.session = await this.memory.agent.open({
        customerId: this.options.customerId,
        agent: this.options.agent ?? "agent",
        externalId: this.options.conversationId,
        channel: this.options.channel,
        tokenBudget: this.options.tokenBudget,
      });
    }
    return this.session;
  }

  /** Close the session and leave a summary for the next conversation. */
  async end(outcome?: string): Promise<AgentSession | null> {
    if (!this.session || this.session.status !== "open") return this.session;
    const closed = await this.memory.agent.close(this.session.id, {
      outcome,
      writeSummary: this.options.writeSummary ?? true,
    });
    this.session = closed.session;
    return this.session;
  }

  /** `start`, your function, then `end` — even when your function throws. */
  async run<T>(fn: (agent: MemoryAgent) => Promise<T>): Promise<T> {
    await this.start();
    try {
      const result = await fn(this);
      await this.end();
      return result;
    } catch (error) {
      await this.end(`ended with ${(error as Error)?.name ?? "an error"}`);
      throw error;
    }
  }

  /** Record the customer's message and return the context to reply with. */
  async beforeTurn(message: string, options: { remember?: boolean } = {}): Promise<TurnContext> {
    const session = await this.start();
    const result: TurnResult = await this.memory.agent.turn(session.id, {
      role: "user",
      content: message,
      remember: options.remember,
      retrieve: true,
    });
    const blocks = [briefingOf(session)];
    if (result.context?.text) blocks.push(`Relevant to this message:\n${result.context.text}`);
    return {
      prompt: blocks.filter(Boolean).join("\n\n"),
      answer: result.answer,
      memoryIds: result.context?.memoryIds ?? [],
      sessionId: session.id,
    };
  }

  /** Record what the agent said. Agent turns are kept, never learned as facts. */
  async afterTurn(reply: string): Promise<void> {
    const session = await this.start();
    await this.memory.agent.turn(session.id, { role: "agent", content: reply, retrieve: false });
  }

  /** `beforeTurn` → `model(prompt, message)` → `afterTurn`, in one call. */
  async respond(
    message: string,
    model: (prompt: string, message: string) => Promise<string>,
  ): Promise<string> {
    const turn = await this.beforeTurn(message);
    const reply = await model(turn.prompt, message);
    await this.afterTurn(reply);
    return reply;
  }

  /** Ask whether an action is allowed, without throwing. Filed with this session. */
  async check(action: string, request?: Record<string, unknown>): Promise<ActionCheck> {
    const session = await this.start();
    return this.memory.guardrails.check({
      customerId: this.options.customerId,
      action,
      request: plain(request),
      sessionId: session.id,
    });
  }

  /**
   * Resolve only if the action is allowed; throw `ActionDeniedError` when a rule refuses
   * and `ApprovalRequiredError` when a person must decide. With `waitMs` it waits for the
   * person and, once they approve, redeems the approval — so it resolves only when the
   * action may really go ahead.
   */
  async guard(
    action: string,
    request?: Record<string, unknown>,
    options: { waitMs?: number; intervalMs?: number } = {},
  ): Promise<ActionCheck> {
    const payload = plain(request);
    const verdict = await this.check(action, payload);
    if (verdict.allowed) return verdict;
    if (verdict.decision === "deny") throw new ActionDeniedError(verdict);
    if (!verdict.approval) throw new ApprovalRequiredError(verdict);

    let approval = verdict.approval;
    if ((options.waitMs ?? 0) > 0 && approval.status === "pending") {
      approval = await this.memory.guardrails.waitForApproval(approval.id, {
        timeoutMs: options.waitMs,
        intervalMs: options.intervalMs,
      });
    }
    if (approval.status === "approved" || approval.status === "rejected") {
      const session = await this.start();
      const redeemed = await this.memory.guardrails.check({
        customerId: this.options.customerId,
        action,
        request: payload,
        approvalId: approval.id,
        sessionId: session.id,
      });
      if (redeemed.allowed) return redeemed;
      throw redeemed.decision === "deny"
        ? new ActionDeniedError(redeemed)
        : new ApprovalRequiredError(redeemed);
    }
    throw new ApprovalRequiredError(verdict);
  }
}
