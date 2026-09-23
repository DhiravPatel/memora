export interface Project {
  id: string;
  organization_id: string;
  name: string;
  api_key_prefix: string;
  settings: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

export interface ProjectWithKey extends Project {
  api_key: string;
}

export interface Customer {
  id: string;
  external_id: string;
  email: string | null;
  name: string | null;
  metadata: Record<string, unknown>;
  last_event_at: string | null;
  created_at: string;
}

export interface Memory {
  id: string;
  customer_id: string;
  type: string;
  content: string;
  importance: number;
  confidence: number;
  status: string;
  source: string;
  sensitivity?: "normal" | "restricted";
  metadata?: Record<string, any>;
  evidence_count: number;
  source_event_ids: string[];
  first_seen_at: string;
  last_seen_at: string;
  expires_at: string | null;
  created_at: string;
}

export interface MemoryVersion {
  id: string;
  previous_content: string | null;
  new_content: string;
  reason: string;
  created_at: string;
}

export interface MemoryDetail extends Memory {
  versions: MemoryVersion[];
  entities: { id: string; name: string; type: string }[];
}

export interface EventRecord {
  id: string;
  customer_id: string;
  event_type: string;
  data: Record<string, unknown>;
  importance: number;
  status: string;
  occurred_at: string;
  processed_at: string | null;
  error: string | null;
  /** Why this event did or did not become a memory. Null for events processed before it existed. */
  outcome: EventExplanation | null;
}

/** One statement the engine found, and what it did (or would do) with it. */
export interface MemoryPlan {
  content: string;
  type: string;
  action: string;
  reason: string;
  importance: number;
  confidence: number;
  similarity: number;
  rule: string | null;
  /** The memory this became. Null in a preview, which creates nothing. */
  memory_id: string | null;
  closest_memory_id: string | null;
  closest_content: string | null;
  sensitivity: string;
  restricted_by: string | null;
  extracted_by: string | null;
}

export interface EntityPlan {
  name: string;
  type: string;
  status: string;
  entity_id: string | null;
}

export interface EventExplanation {
  would_process: boolean;
  /** The field to read first. Null means the pipeline ran to the end. */
  stop_reason: string | null;
  summary: string;
  importance: number;
  threshold: number;
  /** The text the engine read, after redaction. Preview only. */
  text: string | null;
  text_length: number;
  redacted: boolean;
  redactions: { kind: string; count: number }[];
  memories: MemoryPlan[];
  memory_count: number;
  entities: EntityPlan[];
  entity_count: number;
  duration_ms: number;
}

export interface TimelineEntry {
  kind: "event" | "memory";
  id: string;
  title: string;
  detail: string | null;
  occurred_at: string;
  metadata: Record<string, unknown>;
}

export interface Overview {
  project_id: string;
  total_customers: number;
  total_events: number;
  total_memories: number;
  events_processed: number;
  events_pending: number;
  events_failed: number;
  ai_queries: number;
  total_entities: number;
  total_relationships: number;
  memories_by_type: Record<string, number>;
}

export interface UsageResponse {
  totals: Record<string, number>;
  series: { day: string; metric: string; count: number }[];
}

export interface QueryResponse {
  answer: string;
  confidence: number;
  memories: {
    id: string;
    type: string;
    content: string;
    importance: number;
    confidence: number;
    score?: number;
    retrieved_by?: string[];
    source_event_ids?: string[];
  }[];
  sources: { event_id: string }[];
  trace?: Record<string, any>;
}

export interface GraphResponse {
  nodes: {
    id: string;
    label: string;
    type: string;
    mention_count: number;
    is_root: boolean;
  }[];
  edges: {
    id: string;
    source: string;
    target: string;
    label: string;
    confidence: number;
  }[];
}

export interface Page<T> {
  data: T[];
  total: number;
  limit: number;
  offset: number;
  /** Rows this reader's clearance hid. Zero unless a restriction policy applies. */
  withheld?: number;
}

export interface HealthFactor {
  key: string;
  label: string;
  contribution: number;
  count: number;
  memory_ids: string[];
}

export interface Health {
  customer_id: string;
  external_id: string;
  name: string | null;
  score: number;
  band: "healthy" | "watch" | "at_risk" | "critical";
  churn_risk: number;
  explanation: string;
  factors: HealthFactor[];
  memories_considered: number;
  events_considered: number;
  computed_at: string;
}

export interface PortfolioHealth {
  project_id: string;
  customers: Health[];
  bands: string[];
}

export interface MemoryLink {
  id: string;
  link_type: string;
  confidence: number;
  rationale: string | null;
  direction: "outgoing" | "incoming";
  other_memory_id: string;
  other_content: string | null;
  other_type: string | null;
}

export interface CausalChainStep {
  memory_id: string;
  content: string;
  type: string;
  link_type: string;
  confidence: number;
  rationale: string | null;
  occurred_at: string;
}

export interface CausalChain {
  outcome_memory_id: string;
  outcome_content: string;
  occurred_at: string;
  steps: CausalChainStep[];
}

export interface CustomerLinks {
  customer_id: string;
  links: MemoryLink[];
  chains: CausalChain[];
}

export interface Entity {
  id: string;
  type: string;
  name: string;
  external_id: string | null;
  mention_count: number;
  created_at: string;
}

export interface AuditLog {
  id: string;
  action: string;
  actor_type: string;
  actor_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface QueryLog {
  id: string;
  kind: string;
  query: string;
  answer: string | null;
  customer_id: string | null;
  memory_ids: string[];
  latency_ms: number;
  created_at: string;
}

export interface MemoryVersionEntry {
  id: string;
  previous_content: string | null;
  new_content: string;
  reason: string;
  created_at: string;
}

export interface MemoryDetailFull extends Memory {
  metadata: Record<string, unknown>;
  versions: MemoryVersionEntry[];
  entities: { id: string; name: string; type: string }[];
  links: MemoryLink[];
}

export interface ApiKey {
  id: string;
  project_id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_by: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  use_count: number;
  expires_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
  created_at: string;
}

export interface ApiKeyWithSecret extends ApiKey {
  api_key: string;
}

export interface ScopeInfo {
  scope: string;
  description: string;
}

export interface Member {
  id: string;
  email: string;
  name: string | null;
  role: "owner" | "admin" | "member" | "viewer";
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  invited_by: string | null;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
}

export interface InvitationWithToken extends Invitation {
  /** False when the queue was unreachable: no email will arrive, so the link is the only copy. */
  email_queued?: boolean;
  token: string;
  accept_path: string;
}

export interface WebhookEndpoint {
  id: string;
  project_id: string;
  url: string;
  description: string | null;
  event_types: string[];
  is_active: boolean;
  consecutive_failures: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface WebhookEndpointWithSecret extends WebhookEndpoint {
  secret: string;
}

export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event_type: string;
  event_id: string;
  status: "pending" | "succeeded" | "failed" | "disabled";
  attempts: number;
  response_status: number | null;
  response_body: string | null;
  error: string | null;
  duration_ms: number | null;
  scheduled_at: string;
  delivered_at: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface WebhookEventInfo {
  event: string;
  description: string;
}

export interface SettingField {
  key: string;
  label: string;
  group: string;
  kind: "number" | "percent" | "boolean" | "map" | "weights" | "text" | "policies";
  default: unknown;
  help: string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  unit: string | null;
  keys: string[];
}

export interface RestrictionRule {
  kind: "type" | "term" | "pattern";
  value: string;
  label: string;
}

export interface ProjectSettings {
  project_id: string;
  values: Record<string, any>;
  defaults: Record<string, any>;
  groups: { key: string; label: string }[];
  fields: SettingField[];
}

// ------------------------------------------------------------------ foresight

export type Trajectory = "improving" | "steady" | "declining";

export interface Signal {
  key: string;
  label: string;
  direction: "risk" | "opportunity";
  strength: number;
  horizon_days: number;
  rationale: string;
  memory_ids: string[];
  observed: number;
}

export interface SignalPoint {
  captured_on: string;
  health_score: number;
  churn_risk: number;
  expansion_score: number;
  trajectory: Trajectory;
}

export interface SignalReport {
  customer_id: string;
  external_id: string;
  name: string | null;
  trajectory: Trajectory;
  churn_risk: number;
  expansion_score: number;
  confidence: number;
  headline: string;
  health_score: number;
  signals: Signal[];
  measurements: Record<string, number>;
  series: SignalPoint[];
  computed_at: string;
}

export interface PortfolioSignals {
  project_id: string;
  customers: SignalReport[];
  trajectories: Record<string, number>;
}

export interface Recommendation {
  key: string;
  action: string;
  rationale: string;
  category: string;
  urgency: number;
  priority: "now" | "soon" | "when_you_can";
  memory_ids: string[];
  goal_ids: string[];
  signals: string[];
  playbook: string[];
}

export interface Recommendations {
  customer_id: string;
  external_id: string;
  summary: string;
  trajectory: Trajectory;
  churn_risk: number;
  recommendations: Recommendation[];
  computed_at: string;
}

export type GoalStatus = "open" | "progressing" | "achieved" | "stalled" | "abandoned";

export interface GoalEvidence {
  kind: string;
  at: string | null;
  memory_id: string | null;
  event_id: string | null;
  match: number | null;
  cue: string | null;
  note: string | null;
}

export interface Goal {
  id: string;
  customer_id: string;
  statement: string;
  status: GoalStatus;
  progress: number;
  confidence: number;
  keywords: string[];
  memory_id: string | null;
  evidence: GoalEvidence[];
  opened_at: string;
  last_signal_at: string;
  closed_at: string | null;
  closed_reason: string | null;
  overridden: boolean;
  metadata: Record<string, unknown>;
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

export interface PriorSession {
  id: string;
  agent: string;
  summary: string;
  turn_count: number;
  started_at: string;
  closed_at: string | null;
}

export interface SessionContext {
  text: string;
  memory_ids: string[];
  token_estimate: number;
  truncated: boolean;
  prior_sessions: PriorSession[];
}

export interface Turn {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  occurred_at: string;
  event_id: string | null;
  retrieved_memory_ids: string[];
}

export interface AgentSession {
  id: string;
  project_id: string;
  customer_id: string;
  external_id: string | null;
  agent: string;
  channel: string | null;
  status: "open" | "closed" | "expired";
  turn_count: number;
  started_at: string;
  last_active_at: string;
  closed_at: string | null;
  summary: string | null;
  summary_memory_id: string | null;
  memory_ids: string[];
  resumed: boolean;
  context: SessionContext | null;
  turns: Turn[];
}

export interface LearnedTerm {
  term: string;
  synonym: string;
  /** Normalised PMI, 0-1: 1 means the two only ever appear together. Curated pairs are 1. */
  score: number;
  /** How many memories back the pair. Zero for a curated entry — a person is the evidence. */
  support: number;
  source: "mined" | "curated";
  status: "active" | "rejected";
  note: string | null;
  mined_at: string;
}

export interface Vocabulary {
  project_id: string;
  terms: LearnedTerm[];
  total: number;
  curated: number;
  rejected: number;
  last_mined_at: string | null;
}

/** Everything worth knowing about one customer, from a single call.
 *
 * `sections` is a map rather than eleven fixed fields, so a filtered response has fewer
 * keys instead of more nulls — "no open problems" stays distinguishable from "not asked".
 */
export interface Customer360 {
  customer: Record<string, any>;
  summary: string;
  sections: Record<string, any>;
  /** Memories this reader's clearance hid, across every section. */
  withheld: number;
  generated_at: string;
}
