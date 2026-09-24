/** Shared types mirroring the public API contract. */

export type MemoryType =
  | "fact"
  | "preference"
  | "problem"
  | "goal"
  | "behavior"
  | "relationship"
  | "subscription"
  | "feedback"
  | "intent"
  | "summary";

export type MemoryStatus = "active" | "superseded" | "expired" | "deleted";

export interface ClientOptions {
  /** Project API key. Never expose this in a browser. */
  apiKey: string;
  /** Defaults to https://api.aimemorylayer.com */
  baseUrl?: string;
  /** Request timeout in milliseconds. Default 30000. */
  timeoutMs?: number;
  /** Retries for network errors and 429/5xx responses. Default 2. */
  maxRetries?: number;
  /** Extra headers sent with every request. */
  headers?: Record<string, string>;
  fetch?: typeof globalThis.fetch;
  /** Labels this client's runs and checks. A key bound to an agent profile is labelled by
   *  the profile instead. */
  agentName?: string;
}

export interface TrackEventInput {
  customerId: string;
  type: string;
  data?: Record<string, unknown>;
  /** Idempotency key: sending the same id twice stores one event. */
  externalEventId?: string;
  occurredAt?: Date | string;
  customerEmail?: string;
  customerName?: string;
  source?: string;
}

export interface TrackEventResult {
  eventId: string;
  status: "accepted" | "duplicate";
  customerId: string;
  importance: number;
  queued: boolean;
}

export interface Memory {
  id: string;
  customerId: string;
  type: MemoryType;
  content: string;
  importance: number;
  confidence: number;
  status: MemoryStatus;
  evidenceCount: number;
  sourceEventIds: string[];
  firstSeenAt: string;
  lastSeenAt: string;
  expiresAt: string | null;
}

export interface QueriedMemory {
  id: string;
  type: string;
  content: string;
  importance: number;
  confidence: number;
  score?: number;
  retrievedBy?: string[];
  sourceEventIds?: string[];
}

export interface QueryResult {
  answer: string;
  confidence: number;
  memories: QueriedMemory[];
  sources: { eventId: string }[];
  trace?: Record<string, unknown>;
  /** The recorded agent run: `memory.runs.explain(runId)` or `memory.runs.trace(runId)`. */
  runId: string | null;
}

export interface ContextResult {
  customerContext: {
    customer: Record<string, unknown>;
    importantFacts: string[];
    activeProblems: string[];
    preferences: string[];
    goals: string[];
    recentEvents: Record<string, unknown>[];
    relationships: Record<string, unknown>[];
    memories: Record<string, unknown>[];
  };
  promptText?: string | null;
  tokenCount: number;
  truncated: boolean;
  runId: string | null;
}

export interface Customer {
  id: string;
  externalId: string;
  email: string | null;
  name: string | null;
  metadata: Record<string, unknown>;
  lastEventAt: string | null;
  createdAt: string;
}

export interface Page<T> {
  data: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface TimelineEntry {
  kind: "event" | "memory";
  id: string;
  title: string;
  detail: string | null;
  occurredAt: string;
  metadata: Record<string, unknown>;
}

export interface HealthFactor {
  key: string;
  label: string;
  contribution: number;
  count: number;
  memoryIds: string[];
}

export interface CustomerHealth {
  customerId: string;
  externalId: string;
  name: string | null;
  score: number;
  band: "healthy" | "watch" | "at_risk" | "critical";
  churnRisk: number;
  explanation: string;
  factors: HealthFactor[];
  computedAt: string;
}

export interface MemoryLink {
  id: string;
  linkType: string;
  confidence: number;
  rationale: string | null;
  direction: "outgoing" | "incoming";
  otherMemoryId: string;
  otherContent: string | null;
  otherType: string | null;
}

export interface CausalChainStep {
  memoryId: string;
  content: string;
  type: string;
  linkType: string;
  confidence: number;
  rationale: string | null;
  occurredAt: string;
}

export interface CausalChain {
  outcomeMemoryId: string;
  outcomeContent: string;
  occurredAt: string;
  steps: CausalChainStep[];
}

export interface MemoryFeedbackResult {
  memoryId: string;
  verdict: string;
  status: string;
  confidence: number;
  replacementMemoryId: string | null;
}

// ------------------------------------------------------------------ foresight

export type Trajectory = "improving" | "steady" | "declining";
export type SignalDirection = "risk" | "opportunity";
export type Priority = "now" | "soon" | "when_you_can";

export interface Signal {
  key: string;
  label: string;
  direction: SignalDirection;
  /** 0-1. How strongly this observation points the way it points. */
  strength: number;
  /** Roughly how soon this is expected to matter. */
  horizonDays: number;
  /** A sentence describing the observation, built from what was measured. */
  rationale: string;
  memoryIds: string[];
  observed: number;
}

export interface SignalPoint {
  capturedOn: string;
  healthScore: number;
  churnRisk: number;
  expansionScore: number;
  trajectory: Trajectory;
}

export interface SignalReport {
  customerId: string;
  externalId: string;
  name: string | null;
  trajectory: Trajectory;
  churnRisk: number;
  expansionScore: number;
  /** How much evidence the forecast rests on. Low means "not enough history yet". */
  confidence: number;
  headline: string;
  healthScore: number;
  signals: Signal[];
  measurements: Record<string, number>;
  /** Daily history, oldest first. Empty when `series: false` was requested. */
  series: SignalPoint[];
  computedAt: string;
}

export interface Recommendation {
  key: string;
  action: string;
  rationale: string;
  category: string;
  urgency: number;
  priority: Priority;
  memoryIds: string[];
  goalIds: string[];
  signals: string[];
  playbook: string[];
}

export interface Recommendations {
  customerId: string;
  externalId: string;
  summary: string;
  trajectory: Trajectory;
  churnRisk: number;
  recommendations: Recommendation[];
  computedAt: string;
}

export type GoalStatus = "open" | "progressing" | "achieved" | "stalled" | "abandoned";

export interface GoalEvidence {
  kind: string;
  at: string | null;
  memoryId: string | null;
  eventId: string | null;
  match: number | null;
  cue: string | null;
  note: string | null;
}

export interface Goal {
  id: string;
  customerId: string;
  statement: string;
  status: GoalStatus;
  /** 0-1. Only reaches 1 when something closed the goal. */
  progress: number;
  confidence: number;
  keywords: string[];
  memoryId: string | null;
  evidence: GoalEvidence[];
  openedAt: string;
  lastSignalAt: string;
  closedAt: string | null;
  closedReason: string | null;
  /** True when a person set the status; the tracker then leaves the goal alone. */
  overridden: boolean;
}

export interface GoalSummary {
  total: number;
  open: number;
  progressing: number;
  achieved: number;
  stalled: number;
  abandoned: number;
  summary: string;
}

// -------------------------------------------------------------- agent memory

export type SessionStatus = "open" | "closed" | "expired";
export type TurnRole = "user" | "agent" | "system";

export interface PriorSession {
  id: string;
  agent: string;
  summary: string;
  turnCount: number;
  startedAt: string;
  closedAt: string | null;
}

export interface SessionContext {
  /** Drop this straight into your prompt. */
  text: string;
  memoryIds: string[];
  tokenEstimate: number;
  truncated: boolean;
  /** What earlier conversations with this customer established. */
  priorSessions: PriorSession[];
}

export interface Turn {
  id: string;
  role: TurnRole;
  content: string;
  occurredAt: string;
  eventId: string | null;
  retrievedMemoryIds: string[];
}

export interface AgentSession {
  id: string;
  projectId: string;
  customerId: string;
  externalId: string | null;
  agent: string;
  channel: string | null;
  status: SessionStatus;
  turnCount: number;
  startedAt: string;
  lastActiveAt: string;
  closedAt: string | null;
  summary: string | null;
  summaryMemoryId: string | null;
  memoryIds: string[];
  /** True when an existing session was resumed rather than a new one created. */
  resumed: boolean;
  context: SessionContext | null;
  turns: Turn[];
}

export interface TurnResult {
  sessionId: string;
  turn: Turn;
  context: SessionContext | null;
  /** An evidence-backed answer to the customer's turn, if one could be composed. */
  answer: string | null;
  answerConfidence: number | null;
  eventId: string | null;
  turnCount: number;
}

/** One statement the engine found in an event, and what it would do with it. */
export interface MemoryPlan {
  content: string;
  type: string;
  /** create, merge, update, supersede, conflict or ignore. */
  action: string;
  reason: string;
  importance: number;
  confidence: number;
  similarity: number;
  rule: string | null;
  /** The memory this became. Null in a preview, which creates nothing. */
  memoryId: string | null;
  closestMemoryId: string | null;
  closestContent: string | null;
  sensitivity: string;
  restrictedBy: string | null;
  extractedBy: string | null;
}

export interface EntityPlan {
  name: string;
  type: string;
  status: string;
  entityId: string | null;
}

/** Why an event did, or would, become a memory. */
export interface EventExplanation {
  wouldProcess: boolean;
  /** Read this first. When set, nothing else happened and this says why. */
  stopReason: string | null;
  summary: string;
  importance: number;
  threshold: number;
  /** The text the engine read, after redaction. Preview only. */
  text: string | null;
  textLength: number;
  redacted: boolean;
  redactions: { kind: string; count: number }[];
  memories: MemoryPlan[];
  memoryCount: number;
  entities: EntityPlan[];
  entityCount: number;
  durationMs: number;
}

/** Everything worth knowing about one customer, from a single call.
 *
 * `sections` is a map rather than eleven fixed fields: a caller that asks for three
 * sections gets three keys, so "no open problems" stays distinguishable from "I did not
 * look at problems".
 */
export interface Customer360 {
  customer: Record<string, unknown>;
  summary: string;
  sections: Record<string, any>;
  /** Memories this caller's clearance hid, across every section. */
  withheld: number;
  generatedAt: string;
}

/** A condition evaluated against one customer. Act on `matched`; unknown counts as false. */
export interface ConditionResult {
  condition: string;
  outcome: "true" | "false" | "unknown";
  matched: boolean;
  explanation: string;
  evidence: string[];
  leaves: unknown[];
  withheldFacts: string[];
}

/** Where a customer is in the project's lifecycle, and why. */
export interface LifecycleState {
  /** The lifecycle track: "lifecycle" is the primary one. */
  track: string;
  /** The decisive clauses in words: "3 unresolved problems", "activity down 47%". */
  reasons: string[];
  state: string;
  previousState: string | null;
  enteredAt: string;
  source: string;
  transition: string | null;
  reason: string | null;
  evidence: string[];
  pinned: boolean;
  pinnedUntil: string | null;
}

// ------------------------------------------------------------- what changed (§26 4.1)

/** One thing that changed about a customer: before, after, when, and the evidence. */
export interface Change {
  /** subscription, lifecycle, health, risk, trajectory, problem, intent, preference, goal,
   *  feedback, relationship, fact, memory, signal or activity. */
  type: string;
  /** What happened to it: opened, resolved, recurring, changed, moved, crossed, … */
  kind: string;
  title: string;
  before: string | null;
  after: string | null;
  detectedAt: string;
  evidence: string[];
  source: string;
  track: string | null;
  /** For lifecycle moves: the decisive clauses in words. */
  reasons: string[];
  detail: Record<string, unknown>;
  importance: number;
}

/** The customer at one end of a window. `state` is null when nothing was recorded yet. */
export interface CustomerAt {
  at: string;
  live: boolean;
  snapshotId: string | null;
  takenAt: string | null;
  state: Record<string, unknown> | null;
  description: string | null;
}

export interface CustomerChanges {
  customerId: string;
  /** One sentence to read before a call: "In the last 7 days: upgraded to Pro; …". */
  summary: string;
  changes: Change[];
  window: {
    since: string;
    until: string;
    /** span, time, snapshot, last_session, last_run or default. */
    basis: string;
    value: string | null;
    found: boolean;
    note: string | null;
    label: string;
  };
  counts: Record<string, number>;
  total: number;
  truncated: boolean;
  /** Changes about records this key may not read. */
  withheld: number;
  then: CustomerAt;
  now: CustomerAt;
}

export interface CustomerComparison {
  customerId: string;
  then: CustomerAt;
  now: CustomerAt;
  differences: {
    fact: string;
    before: unknown;
    after: unknown;
    added?: string[] | null;
    removed?: string[] | null;
  }[];
  summary: string;
}

// ------------------------------------------------------------------ agents (§26 3)

export type Decision = "allow" | "require_approval" | "deny";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "used";

export interface GuardrailReason {
  rule: string;
  /** profile, builtin, project or approval */
  source: string;
  decision: Decision;
  explanation: string;
  evidence: string[];
  evaluation?: Record<string, unknown> | null;
}

export interface Approval {
  id: string;
  customerId: string;
  checkId: string;
  agent: string | null;
  action: string;
  request: Record<string, unknown>;
  reasons: GuardrailReason[];
  status: ApprovalStatus;
  note: string | null;
  decidedBy: string | null;
  decidedAt: string | null;
  usedAt: string | null;
  expiresAt: string;
  createdAt: string;
  /** The memories the reasons cite, in their own words, as the reviewer may read them. */
  evidenceMemories?: { id: string; type: string | null; content: string | null; status: string | null }[];
  withheldEvidence?: number;
  customer?: Record<string, unknown> | null;
  /** The gateway action waiting on this approval, if any. */
  actionId?: string | null;
}

/** An action through the approval gateway (§26 4.5). `nextStep` says what to do now. */
export interface AgentAction {
  id: string;
  customerId: string;
  action: string;
  request: Record<string, unknown>;
  status: "allowed" | "pending_approval" | "denied" | "done" | "failed" | "cancelled" | "expired";
  decision: Decision;
  summary: string;
  nextStep: string;
  reasons: GuardrailReason[];
  approval: Approval | null;
  checkId: string | null;
  agent: string | null;
  idempotencyKey: string | null;
  outcomeNote: string | null;
  externalRef: string | null;
  createdAt: string;
  completedAt: string | null;
}

export interface ActionCheck {
  id: string;
  customerId: string;
  action: string;
  decision: Decision;
  allowed: boolean;
  /** The sentence to tell a person — or the model — why. */
  summary: string;
  reasons: GuardrailReason[];
  evidence: string[];
  approval: Approval | null;
  agent: string | null;
  profile: string | null;
  request: Record<string, unknown>;
  checkedAt: string;
}

export interface AgentRun {
  id: string;
  kind: "query" | "context";
  customerId: string | null;
  query: string;
  answer: string | null;
  agent: string | null;
  sessionId: string | null;
  snapshotId: string | null;
  memoryCount: number;
  citedCount: number;
  withheld: number;
  createdAt: string;
  memoryIds?: string[];
  trace?: Record<string, unknown>;
}

export interface RunMemory {
  id: string;
  rank: number;
  type: string | null;
  score: number;
  strategies: string[];
  cited: boolean;
  scores: Record<string, unknown>;
  visible: boolean;
  /** The words as they were when the agent saw them. */
  contentThen: string | null;
  contentNow: string | null;
  statusNow: string | null;
  changedSince: { at: string; reason: string; content: string | null }[];
}

export interface RunExplanation {
  run: AgentRun;
  narrative: string[];
  memories: RunMemory[];
  heldBack: Record<string, unknown>;
  stateThen: Record<string, unknown> | null;
  checks: ActionCheck[];
}

/** A memory the agent was given, with the verdict: `cited` (the answer rests on it),
 *  `given` (handed over in a context) or `not_cited`. */
export interface TraceGiven extends RunMemory {
  verdict: "cited" | "given" | "not_cited";
  why: string;
}

/** A memory the question matched that the agent was not given, and why. */
export interface TraceIgnored {
  id: string;
  type: string | null;
  /** below_cut, type_cap, token_budget, section_cap, duplicate, superseded, expired,
   *  withheld_restricted or withheld_profile. */
  reason: string;
  why: string;
  visible: boolean;
  content: string | null;
  score: number | null;
  position: number | null;
  match: number | null;
  supersededBy: string | null;
  replacementRank: number | null;
}

/** Why did my agent do this? (§26 4.4) */
export interface RunTrace {
  run: AgentRun;
  question: string;
  narrative: string[];
  given: TraceGiven[];
  ignored: TraceIgnored[];
  decision: {
    kind: "answer" | "context";
    answer: string | null;
    strategy: string | null;
    confidence: number | null;
    reasoning: string[];
    evidence: string[];
    tokenCount: number | null;
    tokenBudget: number | null;
    truncated: boolean | null;
  };
  cut: Record<string, unknown>;
  heldBack: Record<string, unknown>;
  stateThen: Record<string, unknown> | null;
  checks: ActionCheck[];
  /** False for runs recorded before traces kept what was considered. */
  recorded: boolean;
}

export interface AgentProfile {
  id: string;
  name: string;
  description: string | null;
  readableTypes: string[];
  canReadRestricted: boolean;
  allowedActions: string[];
  deniedActions: string[];
  keys: number;
}
