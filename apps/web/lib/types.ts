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
  agent_profile_id: string | null;
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
  kind:
    | "number"
    | "percent"
    | "boolean"
    | "map"
    | "weights"
    | "text"
    | "policies"
    | "lifecycle"
    | "guardrails";
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

// ------------------------------------------------------- facts & conditions (§26 1.1)

export interface FactSpec {
  name: string;
  type: "number" | "string" | "enum" | "boolean" | "list" | "terms" | "any";
  description: string;
  values: string[];
  unit: string | null;
  operators: string[];
}

export interface FactCatalog {
  facts: FactSpec[];
  metadata_prefix: string;
  examples: string[];
}

export interface ConditionValidation {
  valid: boolean;
  text: string | null;
  ast: Record<string, unknown> | null;
  facts: string[];
  error: string | null;
  /** Character offset of a parse error, for underlining. */
  position: number | null;
}

export interface ConditionLeaf {
  fact: string;
  op: string;
  expected: unknown;
  actual: unknown;
  outcome: "true" | "false" | "unknown";
  note: string;
  evidence: string[];
  description: string;
}

export interface ConditionEvaluation {
  outcome: "true" | "false" | "unknown";
  /** What to act on — unknown counts as not matched. */
  matched: boolean;
  explanation: string;
  evidence: string[];
  decisive: ConditionLeaf[];
  leaves: ConditionLeaf[];
}

export interface ConditionEvaluationResult {
  customer_id: string;
  condition: string;
  evaluation: ConditionEvaluation;
  withheld_facts: string[];
}

export interface CustomerFacts {
  customer_id: string;
  values: Record<string, unknown>;
  evidence: Record<string, string[]>;
  withheld_facts: string[];
  computed_at: string;
}

// -------------------------------------------------------------- lifecycle (§26 1.3)

export interface CustomerStateRecord {
  id: string;
  state: string;
  previous_state: string | null;
  entered_at: string;
  exited_at: string | null;
  source: "initial" | "auto" | "manual";
  transition: string | null;
  reason: string | null;
  evidence: string[];
  pinned: boolean;
  pinned_until: string | null;
  actor_id: string | null;
  evaluation: Partial<ConditionEvaluation>;
}

export interface CurrentState {
  customer_id: string;
  enabled: boolean;
  current: CustomerStateRecord | null;
  states: string[];
}

export interface LifecycleTransition {
  name: string;
  from: string[];
  to: string;
  when: string;
}

export interface LifecycleDefinition {
  enabled?: boolean;
  states: string[];
  initial: string;
  transitions: LifecycleTransition[];
}

export interface LifecycleOverview extends Partial<LifecycleDefinition> {
  enabled: boolean;
  counts: Record<string, number>;
}

export interface StateRefresh {
  customer_id: string;
  state: string | null;
  moved: boolean;
  transitions: { from: string | null; to: string; transition: string }[];
  snapshot_id: string | null;
}

// -------------------------------------------------------------- snapshots (§26 1.2)

export interface SnapshotChange {
  fact: string;
  before: unknown;
  after: unknown;
  added?: string[];
  removed?: string[];
}

export interface SnapshotSummary {
  id: string;
  taken_at: string;
  reason: string;
  event_id: string | null;
  health_score: number | null;
  health_band: string | null;
  state: string | null;
  plan: string | null;
  trajectory: string | null;
  open_problems: number;
  churn_risk: number | null;
  expansion_score: number | null;
  changes: SnapshotChange[];
}

export interface Snapshot extends SnapshotSummary {
  facts: Record<string, unknown>;
  evidence: Record<string, string[]>;
  withheld_facts: string[];
}

// ------------------------------------------------------------ quality (§26 2.2)

export interface QualityComponent {
  key: string;
  label: string;
  score: number | null;
  detail: string;
}

export interface QualityDiagnostic {
  key: string;
  severity: "info" | "warning" | "critical";
  title: string;
  detail: string;
  fix: Record<string, any>;
  examples: any[];
}

export interface EventTypeQuality {
  event_type: string;
  total: number;
  processed: number;
  produced: number;
  below_threshold: number;
  no_text: number;
  failed: number;
  importance: number;
}

export interface QualityReport {
  window_days: number;
  score: number | null;
  components: QualityComponent[];
  metrics: {
    events: {
      total: number;
      processed: number;
      skipped: number;
      failed: number;
      backlog: number;
      produced: number;
      yield: number | null;
      by_type: EventTypeQuality[];
    };
    consolidation: {
      statements: number;
      creates: number;
      merges: number;
      conflicts: number;
      near_miss_creates: number;
      near_miss_rate: number | null;
      conflict_rate: number | null;
      near_miss_examples: { content: string; closest_content: string; similarity: number }[];
    };
    memories: {
      active: number;
      avg_confidence: number | null;
      low_confidence: number;
      stale: number;
      expiring_soon: number;
      restricted: number;
    };
    searches: {
      total: number;
      with_unknown_terms: number;
      unknown_rate: number | null;
      unknown_terms: string[];
      examples: string[];
    };
    evaluation: {
      set: string;
      run_id: string;
      recall_at_5: number | null;
      mrr: number | null;
      citation_hit_rate: number | null;
      regressed: boolean;
    } | null;
    settings: { min_event_importance: number; consolidation_similarity: number };
  };
  diagnostics: QualityDiagnostic[];
  computed_at: string;
}

// --------------------------------------------------------- evaluation (§26 2.1)

export interface EvalRunSummary {
  id: string;
  set_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  label: string | null;
  k: number;
  metrics: {
    cases?: number;
    scored?: number;
    recall?: Record<string, number>;
    hit?: Record<string, number>;
    mrr?: number;
    citation_hit_rate?: number;
    misses?: string[];
  };
  comparison: {
    recall?: Record<string, number>;
    hit?: Record<string, number>;
    mrr?: number;
    citation_hit_rate?: number;
    newly_missed?: string[];
    newly_found?: string[];
    regressed?: boolean;
  } | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface EvalCaseResult {
  case_id: string;
  question: string;
  error: string | null;
  first_rank: number | null;
  cited_hit: boolean;
  expected: {
    kind: string;
    target: string;
    rank: number | null;
    memory_id: string | null;
    cited: boolean;
  }[];
  retrieved: { id: string; content: string; score: number; strategies: string[]; cited: boolean }[];
  answer: string | null;
}

export interface EvalRun extends EvalRunSummary {
  settings: Record<string, unknown>;
  results: EvalCaseResult[];
  baseline_run_id: string | null;
}

export interface EvalCase {
  id: string;
  customer_id: string;
  question: string;
  expected_memory_ids: string[];
  expected_phrases: string[];
  notes: string | null;
  source: string;
  created_at: string;
}

export interface EvalSet {
  id: string;
  name: string;
  description: string | null;
  cases: number;
  latest_run: EvalRunSummary | null;
  created_at: string;
}

export interface EvalSetDetail extends EvalSet {
  case_list: EvalCase[];
  runs: EvalRunSummary[];
}

export interface EvalSuggestion {
  customer_id: string;
  question: string;
  asked_at: string;
  answer: string | null;
  retrieved: { id: string; type: string; content: string }[];
}

// ------------------------------------------------------------------ agents (§26 phase 3)

export type Decision = "allow" | "require_approval" | "deny";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "used";

export interface GuardrailReason {
  rule: string;
  source: "profile" | "builtin" | "project" | "approval" | string;
  decision: Decision;
  explanation: string;
  evidence: string[];
  evaluation?: ConditionEvaluation | null;
}

export interface Approval {
  id: string;
  customer_id: string;
  check_id: string;
  agent: string | null;
  action: string;
  request: Record<string, unknown>;
  reasons: GuardrailReason[];
  status: ApprovalStatus;
  note: string | null;
  decided_by: string | null;
  decided_at: string | null;
  used_at: string | null;
  expires_at: string;
  created_at: string;
}

export interface AgentCheck {
  id: string;
  customer_id: string;
  action: string;
  decision: Decision;
  allowed: boolean;
  summary: string;
  reasons: GuardrailReason[];
  evidence: string[];
  approval: Approval | null;
  agent: string | null;
  profile: string | null;
  request: Record<string, unknown>;
  snapshot_id: string | null;
  session_id: string | null;
  checked_at: string;
}

export interface AgentProfile {
  id: string;
  name: string;
  description: string | null;
  readable_types: string[];
  can_read_restricted: boolean;
  allowed_actions: string[];
  denied_actions: string[];
  keys: number;
  created_at: string;
  updated_at: string;
}

export interface GuardrailCatalog {
  actions: { action: string; family: string }[];
  builtin_rules: { rule: string; description: string }[];
  decisions: Decision[];
}

export interface GuardrailRule {
  name: string;
  actions: string[];
  when: string;
  decision: "deny" | "require_approval";
  message: string;
}

export interface GuardrailSettings {
  disabled: string[];
  rules: GuardrailRule[];
  approval_ttl_hours: number;
}

export interface AgentActivity {
  window_days: number;
  checks: Partial<Record<Decision, number>>;
  approvals: Partial<Record<ApprovalStatus, number>>;
  top_rules: { rule: string; count: number }[];
  runs: number;
}

export interface AgentRunSummary {
  id: string;
  kind: "query" | "context";
  customer_id: string | null;
  query: string;
  answer: string | null;
  agent: string | null;
  session_id: string | null;
  api_key_id: string | null;
  snapshot_id: string | null;
  memory_count: number;
  cited_count: number;
  withheld: number;
  latency_ms: number;
  created_at: string;
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
  content_then: string | null;
  content_now: string | null;
  status_now: string | null;
  changed_since: { at: string; reason: string; content: string | null }[];
}

export interface RunExplanation {
  run: AgentRunSummary;
  narrative: string[];
  memories: RunMemory[];
  held_back: {
    withheld?: number;
    cleared?: boolean | null;
    readable_types?: string[] | null;
    profile?: string | null;
    hidden_from_you?: number;
    dropped_by_budget?: string[];
  };
  state_then: Snapshot | null;
  checks: AgentCheck[];
}
