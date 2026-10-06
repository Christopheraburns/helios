export interface ApiHealth {
  status: "ok";
}

export interface PrincipalIdentity {
  id: string;
  issuer: string;
  subject: string;
  display_name: string;
  kind: string;
}

export interface ApiDiagnostics {
  status: "ok";
  principal: PrincipalIdentity;
  accessible_organization_count: number;
}

export type ModelProviderId =
  | "anthropic"
  | "mistral"
  | "bedrock"
  | "openai";

export interface ModelProviderSettings {
  source: "environment" | "session";
  provider: ModelProviderId | null;
  model: string | null;
  api_key_configured: boolean;
  providers: Array<{
    id: ModelProviderId;
    available: boolean;
    /** The model the deployment configures for this provider, if any. */
    default_model?: string | null;
    /** True when the deployment supplies a key, so the user need not enter one. */
    key_configured?: boolean;
  }>;
}

export interface ModelProviderSettingsWrite {
  provider: ModelProviderId;
  model: string;
  api_key: string;
}

export interface MCPSettings {
  source: "environment" | "session";
  max_tool_rounds: number;
  default_max_tool_rounds: number;
  limits: {
    min_tool_rounds: number;
    max_tool_rounds: number;
  };
}

export interface MCPToolSummary {
  name: string;
  description: string;
}

export interface MCPStatus {
  status: "available" | "unavailable";
  checked_at: string;
  message: string;
  timeout_seconds: number | null;
  server: {
    server_name?: string | null;
    server_version?: string | null;
    protocol_version?: string | null;
  };
  tools: MCPToolSummary[];
}

export interface TraceRun {
  id: string;
  request_id: string | null;
  conversation_id: string | null;
  principal_id: string;
  organization_id: string;
  model_id: string;
  purpose: "conversation" | "evaluation";
  question_id: string | null;
  question: string;
  llm_provider: string;
  llm_model: string;
  prompt_version: string;
  status: "running" | "completed" | "failed" | "cancelled";
  termination_reason: string | null;
  answer: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  tokens_in: number;
  tokens_out: number;
  semantic_revision_id?: string | null;
}

export interface TraceSpan {
  id: string;
  run_id: string;
  parent_span_id: string | null;
  sequence: number;
  component: string;
  kind: string;
  name: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
  input: unknown;
  output: unknown;
  attributes: Record<string, unknown>;
  error: string | null;
}

export interface TraceCollection {
  items: TraceRun[];
  page: {
    offset: number;
    limit: number;
    returned: number;
    total: number;
    has_more: boolean;
  };
  available_actions: string[];
}

export interface TraceDetail {
  run: TraceRun;
  spans: TraceSpan[];
  semantic_evidence?: SemanticTraceEvidence;
}

/** One passage the document tools returned for an answer. */
export interface TraceDocumentPassage {
  id: string;
  asset_id: string | null;
  text: string;
  locator: Record<string, unknown>;
  relevance: number | null;
  claim?: string | null;
  entities: Array<{ class: string; name: string; keys?: string[] }>;
}

/** The document side of an answer path: searches, documents found by type,
 * and the document keys that were used as filters in the approved query. */
export interface TraceDocumentEvidence {
  searches: Array<{
    id: string;
    tool: string;
    query: string;
    status: string;
    result_count: number;
  }>;
  groups: Array<{
    id: string;
    type: string;
    count: number;
    search_ids: string[];
    passages: TraceDocumentPassage[];
  }>;
  bridges: Array<{
    id: string;
    group_id: string;
    passage_id: string;
    key: string;
    field: string;
    value: string;
    entity: { class: string | null; name: string | null };
  }>;
}

export interface SemanticTraceEvidence {
  status: "complete" | "incomplete";
  incomplete_reasons: string[];
  question: string;
  assistant: {
    provider: string;
    model: string;
    prompt_version: string;
  };
  revision: {
    id: string;
    sha256: string;
    ossie_version: string | null;
    discovery_run_id: string | null;
    published_at: string;
    verified: boolean;
    verification_error: string | null;
  } | null;
  tools: Array<{
    id: string;
    name: string;
    status: string;
    error: string | null;
    arguments: Record<string, unknown>;
  }>;
  semantic_objects: Array<{
    id: string;
    kind: string;
    name: string;
    description: string;
    physical_name?: string;
    ossie_pointer: string;
    canvas_element_id?: string;
  }>;
  datasets: Array<{
    id: string;
    semantic_dataset: string | null;
    physical_name: string;
    data_source_id: string | null;
  }>;
  query: {
    id: string;
    sql: string;
    columns: string[];
    row_count: number | null;
  } | null;
  documents?: TraceDocumentEvidence | null;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    type: string;
  }>;
  answer: string | null;
  error: {
    reason: string | null;
    message: string | null;
  } | null;
}

export interface EvaluationResult {
  id: string;
  question_id: string;
  variant: "baseline" | "candidate";
  repetition: number;
  trace_run_id: string;
  accurate: boolean;
  completed: boolean;
  metrics: Record<string, unknown>;
}

export interface EvaluationRun {
  id: string;
  principal_id: string;
  organization_id: string;
  model_id: string;
  suite_id: string;
  suite_version: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  repetitions: number;
  baseline: { provider: string; model: string };
  candidate: { provider: string; model: string };
  max_tool_rounds: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  metrics: Record<string, Record<string, number>>;
  cancel_requested: boolean;
  results: EvaluationResult[];
}

export interface EvaluationCollection {
  items: EvaluationRun[];
  available_actions: string[];
}

export interface OrganizationSummary {
  id: string;
  name: string;
  available_actions: string[];
}

export interface OrganizationsResponse {
  organizations: OrganizationSummary[];
  count: number;
}

export interface DataSourceReference {
  data_source_id: string;
  selected_assets: string[];
}

export interface ModelSummary {
  id: string;
  organization_id: string;
  name: string;
  description: string;
  data_sources: DataSourceReference[];
  available_actions: string[];
}

export interface ModelOverviewDataSource extends DataSourceReference {
  name: string;
  connector: string | null;
}

export interface ModelOverview
  extends Omit<ModelSummary, "data_sources"> {
  status: string;
  creator: {
    id: string;
    display_name: string;
  };
  created_at: string;
  updated_at: string;
  data_sources: ModelOverviewDataSource[];
  summary: {
    dataset_count: number;
    relationship_count: number;
    concept_count: number;
    metric_count: number;
  };
  lifecycle: {
    publication_state: "configured" | "proposed" | "published";
    discovery_status:
      | "not_started"
      | "unavailable"
      | "started"
      | "harvest_complete"
      | "profile_complete"
      | "proposals_ready";
    review_status: "not_available" | "pending" | "complete";
    unresolved_review_items: number | null;
    latest_run_id: string | null;
  };
}

export type HealthState =
  | "healthy"
  | "degraded"
  | "unavailable"
  | "unknown";

export interface HealthComponent {
  id: string;
  label: string;
  status: HealthState;
  description: string;
}

export interface RecentHealthActivity {
  run_id: string;
  completed_at: string | null;
}

export interface ModelSystemStatus {
  model_id: string;
  status: HealthState;
  checked_at: string;
  components: HealthComponent[];
  details_available: boolean;
  details: HealthComponent[];
  recent_activity: {
    discovery: RecentHealthActivity | null;
    profile: RecentHealthActivity | null;
  };
  issues: string[];
}

export interface ModelsResponse {
  organization_id: string | null;
  models: ModelSummary[];
  count: number;
}

export type RunLifecycleStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "cancelled";

export type DiscoveryPhaseId = "harvest" | "profile" | "propose";

export interface DiscoveryRunPhase {
  id: DiscoveryPhaseId;
  name: "Harvest" | "Profile" | "Propose";
  status: "completed" | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  counts: Record<string, number>;
  available: boolean;
}

export interface DiscoveryRun {
  id: string;
  type: string;
  model_id: string;
  status: RunLifecycleStatus | null;
  progress: number | null;
  initiator: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  warnings: unknown[];
  errors: unknown[];
  stages: Record<DiscoveryPhaseId, boolean>;
  phases: DiscoveryRunPhase[];
  counts: {
    discovered: Record<string, number>;
    profiled: Record<string, number>;
    proposed: Record<string, number>;
  };
  data_source?: {
    engine: string | null;
    databases: string[];
  };
  provenance: {
    llm: Record<string, unknown> | null;
  };
  missing?: boolean;
  available_actions?: string[];
}

export interface DiscoveryRunsResponse {
  model_id: string;
  runs: DiscoveryRun[];
  available_actions: string[];
}

export interface TableColumnProfile {
  type?: string;
  null_rate?: number;
  ndv?: number;
  ndv_exact?: number;
  min?: unknown;
  max?: unknown;
  glossary_terms: string[];
  [key: string]: unknown;
}

export interface RelationshipEvidence {
  from: string;
  from_column?: string;
  to: string;
  to_column?: string;
  name_score?: number;
  to_rows?: number;
  distinct_values?: number;
  unmatched?: number;
  match_ratio?: number;
}

export interface PrimaryKeyCandidate {
  column: string;
  confidence?: number;
}

export interface HistoricalTableSummary {
  table_id: string;
  harvested: boolean;
  profiled: boolean;
  column_count: number;
  row_count: number | null;
  primary_key_candidates: PrimaryKeyCandidate[];
}

export interface HistoricalProfileSummary {
  model_id: string;
  run_id: string;
  profiled_at: string | null;
  engine: string | null;
  tables: HistoricalTableSummary[];
  relationships: RelationshipEvidence[];
  suggested_relationships: RelationshipEvidence[];
  rejected_candidates: RelationshipEvidence[];
  provenance?: {
    artifacts: Array<"harvest" | "profile">;
    run_id: string;
  };
  available_actions: string[];
}

export interface HistoricalTableProfile {
  model_id: string;
  run_id: string;
  table_id: string;
  profiled_at: string | null;
  engine: string | null;
  row_count: number | null;
  column_count: number;
  columns: Record<string, TableColumnProfile>;
  primary_key_candidates: PrimaryKeyCandidate[];
  relationships: RelationshipEvidence[];
  provenance: {
    artifact: "profile";
    run_id: string;
    table_id: string;
  };
  canvas: {
    element_id: string;
    focus_node_id: string;
    lens: "physical";
  };
  available_actions: string[];
}

export interface HeliosGraphNodeDto {
  id: string;
  kind: string;
  label: string;
  status: string;
  confidence: number | null;
  evidence: string | null;
  metadata: Record<string, unknown>;
  permitted_actions: string[];
}

export interface HeliosGraphEdgeDto {
  id: string;
  kind: string;
  source: string;
  target: string;
  status: string;
  confidence: number | null;
  evidence: string | null;
  metadata: Record<string, unknown>;
  permitted_actions: string[];
}

export interface HeliosGraphDto {
  model_id: string;
  organization_id: string;
  nodes: HeliosGraphNodeDto[];
  edges: HeliosGraphEdgeDto[];
  summary: {
    node_count: number;
    edge_count: number;
    node_kinds: string[];
    edge_kinds: string[];
  };
  navigation?: {
    focus_node_id: string | null;
    truncated: boolean;
    authorized_node_count: number;
    authorized_edge_count: number;
    returned_node_count: number;
    returned_edge_count: number;
    total_match_count: number | null;
    hidden_neighbor_count: Record<string, number>;
    expandable_node_ids: string[];
  };
}

export type GraphLens = "physical" | "semantic" | "ontology";

export interface GraphNavigationOptions {
  navigation?: boolean;
  lens?: GraphLens;
  focusNodeId?: string;
  depth?: number;
  includeAttributes?: boolean;
  limit?: number;
  query?: string;
  reviewRunId?: string;
}

export interface GraphElementDetail {
  element_type: "node" | "edge";
  id: string;
  kind: string;
  label: string | null;
  source: string | null;
  target: string | null;
  status: string;
  confidence: number | null;
  evidence: string | null;
  details: Record<string, unknown>;
  available_actions: string[];
}

export type ReviewSection =
  | "datasets"
  | "fields"
  | "relationships"
  | "metrics"
  | "glossary_terms";

export type ReviewDecision = "pending" | "accept" | "reject" | "edit";

export interface DatasetProposal {
  table: string;
  name?: string;
  kind?: "fact" | "dimension" | "bridge" | "lookup" | "other" | string;
  description?: string;
  primary_key?: string[];
  confidence?: number;
  source?: string;
  fields?: FieldProposal[];
}

export interface FieldProposal {
  table: string;
  column: string;
  type?: string;
  name?: string;
  role?: "identifier" | "foreign_key" | "time" | "measure" | "dimension" | "attribute" | string;
  description?: string;
  refers_to?: string | null;
  glossary_terms?: string[];
  proposed_term?: { name?: string; definition?: string } | null;
  confidence?: number;
  source?: string;
}

export interface RelationshipProposal {
  from: string;
  from_column: string;
  to: string;
  to_column: string;
  accepted?: boolean;
  source?: string;
  reason?: string;
  confidence?: number;
  match_ratio?: number;
}

export interface MetricProposal {
  name: string;
  description?: string;
  dataset: string;
  expression?: string;
  source?: string;
  confidence?: number;
}

export interface GlossaryTermProposal {
  name: string;
  definition?: string;
  columns?: string[];
  source?: string;
  confidence?: number;
}

export interface ProposalReviewState {
  decision: ReviewDecision;
  overrides: Record<string, unknown> | null;
  note: string | null;
}

export interface ProposalCanvasMetadata {
  review_run_id: string;
  element_id: string;
  focus_node_id: string;
  related_node_ids?: string[];
  lens: GraphLens;
}

interface ProposalItemBase<S extends ReviewSection, P> {
  id: string;
  section: S;
  proposal: P;
  confidence: number | null;
  provenance: {
    source: string | null;
    llm: Record<string, unknown> | null;
  };
  review: ProposalReviewState;
  canvas: ProposalCanvasMetadata;
  available_actions: Array<"accept" | "reject" | "edit" | "view_in_canvas">;
}

export type ProposalItem =
  | ProposalItemBase<"datasets", DatasetProposal>
  | ProposalItemBase<"fields", FieldProposal>
  | ProposalItemBase<"relationships", RelationshipProposal>
  | ProposalItemBase<"metrics", MetricProposal>
  | ProposalItemBase<"glossary_terms", GlossaryTermProposal>;

export interface ProposalCollection {
  model_id: string;
  run_id: string;
  section: ReviewSection;
  items: ProposalItem[];
  page: {
    offset: number;
    limit: number;
    returned: number;
    total: number;
    has_more: boolean;
  };
  filters: {
    decision: ReviewDecision | null;
    query: string | null;
  };
  reviewed_at: string | null;
  reviewed_by: string | null;
  available_actions: Array<"decide" | "cascade" | "bulk_accept" | "reset">;
}

export interface ProposalCollectionOptions {
  section: ReviewSection;
  decision?: ReviewDecision;
  query?: string;
  offset?: number;
  limit?: number;
}

export interface ReviewSectionCounts {
  accept: number;
  reject: number;
  edit: number;
  pending: number;
  total: number;
}

export interface ReviewSummary {
  model_id: string;
  run_id: string;
  sections: Record<ReviewSection, ReviewSectionCounts>;
  reviewed_at: string | null;
  reviewed_by: string | null;
  preflight_issues: Record<string, string>;
  validation_errors: string[];
  publish_ready: boolean;
  publication: Record<string, unknown> | null;
  available_actions: string[];
}

export interface ReviewDecisionRequest {
  section: ReviewSection;
  element_id: string;
  decision: "accept" | "reject" | "edit";
  overrides?: Record<string, unknown>;
  note?: string;
}

export interface ReviewDecisionResponse {
  ok: true;
  run_id: string;
  section: string;
  element_id: string;
  entry: {
    decision: "accept" | "reject" | "edit";
    overrides?: Record<string, unknown>;
    note?: string;
  };
  reviewed_at: string;
  reviewed_by: string;
  summary: ReviewSummary;
}

export interface ReviewMutationResponse {
  ok: true;
  changed?: number;
  summary: ReviewSummary;
}

export interface ReviewPublicationResponse {
  ok: true;
  model_id: string;
  run_id: string;
  manifest: Record<string, unknown>;
}

export interface GlossarySummary {
  id: string;
  name: string;
  description: string;
  term_count: number;
}

export interface ModelGlossary {
  model_id: string;
  glossary: GlossarySummary | null;
  available_actions: string[];
}

export interface GlossaryTerm {
  id: string;
  name: string;
  definition: string;
  long_description: string;
  abbreviation: string;
  examples: string[];
  status: "published";
  confidence: number | null;
  evidence: string[];
}

export interface GlossaryAssignment {
  id: string;
  name: string;
  type: string;
  canvas_element_id: string;
  canvas_lens: "physical";
}

export interface GlossaryTermDetail extends GlossaryTerm {
  assignments: GlossaryAssignment[];
}

export interface GlossaryTermsResponse {
  model_id: string;
  glossary_id: string;
  items: GlossaryTerm[];
  offset: number;
  limit: number;
  total: number;
  truncated: boolean;
  available_actions: string[];
}

export interface GlossaryTermResponse {
  term: GlossaryTermDetail | GlossaryTerm;
  available_actions: string[];
}

export interface GlossaryTermWrite {
  name: string;
  definition: string;
  long_description: string;
  abbreviation: string;
  examples: string[];
}

export interface GlossaryTermsOptions {
  query?: string;
  status?: "published";
  sort?: "name" | "definition" | "abbreviation";
  direction?: "asc" | "desc";
  offset?: number;
  limit?: number;
}

export interface GlossaryAssignableAsset {
  id: string;
  name: string;
  dataset_id: string | null;
}

export interface GlossaryImportResult {
  ok: true;
  imported: number;
  failed: number;
}

export interface ConversationToolTrace {
  tool: string;
  arguments: Record<string, unknown>;
  result: unknown;
}

export interface ConversationTurn {
  model_id: string;
  answer: string;
  failure?: {
    code: string;
    message: string;
    retryable: boolean;
  } | null;
  tool_trace: ConversationToolTrace[];
  query_result: {
    columns: string[];
    rows: unknown[][];
    sql?: string;
  } | null;
  provenance?: {
    helios?: { api_version?: string | null };
    llm?: { provider?: string | null; model?: string | null };
    mcp?: {
      server_name?: string | null;
      server_version?: string | null;
      protocol_version?: string | null;
    };
  };
  request_id?: string | null;
  trace_run_id?: string | null;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  turn?: ConversationTurn | null;
}

export interface ConversationSummary {
  id: string;
  model_id: string;
  title: string;
  version: number;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface ConversationCollection {
  model_id: string;
  conversations: ConversationSummary[];
}

export interface PersistedConversationTurn {
  conversation: ConversationDetail;
  turn: ConversationTurn;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  request_id: string | null;
  session_id: string | null;
  principal_id: string | null;
  organization_id: string | null;
  model_id: string | null;
  component: string;
  event_type: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  outcome: string;
  severity: string;
  http_status: number | null;
  duration_ms: number | null;
  summary: string;
  details: Record<string, unknown>;
  diagnostics?: Record<string, unknown> | null;
}

export interface AuditEventCollection {
  items: AuditEvent[];
  page: {
    offset: number;
    limit: number;
    returned: number;
    total: number;
    has_more: boolean;
  };
  filters: Record<string, unknown>;
  available_actions: string[];
}

export interface AuditSession {
  session_id: string;
  principal_id: string;
  first_seen_at: string;
  last_seen_at: string;
  event_count: number;
  organization_id: string | null;
}

export interface AuditSessionCollection {
  sessions: AuditSession[];
  available_actions: string[];
}

export interface AuditEventOptions {
  organizationId?: string;
  principalId?: string;
  sessionId?: string;
  modelId?: string;
  component?: string;
  eventType?: string;
  outcome?: string;
  severity?: string;
  includeAll?: boolean;
  offset?: number;
  limit?: number;
}

export interface ClientAuditEvent {
  action:
    | "navigation.view"
    | "context.organization_select"
    | "context.model_select"
    | "workspace.collapse"
    | "workspace.expand"
    | "canvas.lens_change"
    | "canvas.node_focus"
    | "canvas.node_select"
    | "activity.filter_change";
  path?: string;
  resource_type?:
    | "application"
    | "organization"
    | "model"
    | "workspace"
    | "canvas"
    | "node"
    | "activity";
  resource_id?: string;
  model_id?: string;
}

export interface AssistantJourneyStep {
  id: string;
  title: string;
  description: string;
  route: string;
  params: Record<string, string>;
  doc_slug: string | null;
  status: "done" | "todo" | "external";
  note: string | null;
}

export interface AssistantJourney {
  id: string;
  title: string;
  description: string;
  progress: { done: number; total: number };
  next_step_id: string | null;
  steps: AssistantJourneyStep[];
}

export interface AssistantWorkspaceState {
  organization: { id: string; name: string };
  selected_model_id: string | null;
  llm_provider: {
    configured: boolean;
    provider: string | null;
    model: string | null;
  };
  mcp: { configured: boolean };
  ontology: { version_count: number; active_version: string | null };
  data_sources: Array<{
    id: string;
    name: string;
    connector: string;
    crawl_enabled: boolean;
    last_crawl: {
      crawl_run_id: string;
      status: string;
      started_at: string;
    } | null;
  }>;
  crawl_runs: {
    total: number;
    last: {
      crawl_run_id: string;
      status: string;
      started_at: string;
      source: string;
    } | null;
  };
  models: Array<{
    id: string;
    name: string;
    data_source_count: number;
    lifecycle: ModelOverview["lifecycle"];
    summary: ModelOverview["summary"];
    has_conversations: boolean;
    available_actions: string[];
  }>;
  journeys: AssistantJourney[];
}

export interface AssistantTurnRequest {
  organization: string;
  model: string | null;
  message: string;
  history?: Array<{ role: "user" | "assistant"; content: string }>;
  location?: { pathname: string; search: string };
}

export interface AssistantAction {
  type: "navigate";
  route: string;
  params: Record<string, string>;
  label: string;
}

export interface AssistantTurnResponse {
  answer: string;
  actions: AssistantAction[];
  tool_trace: Array<{
    name: string;
    arguments: Record<string, unknown>;
    ok: boolean;
  }>;
  provenance: { llm: { provider: string; model: string } };
  request_id: string | null;
  trace_run_id: string | null;
}

export class AuthenticationError extends Error {}
export class AuthorizationError extends Error {}
export class ConflictError extends Error {}

export class ApiUnavailableError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
  }
}

/** HTTP 503: the API is reachable but a dependency (such as the LLM provider) is not configured. */
export class ServiceUnavailableError extends ApiUnavailableError {}

function apiErrorMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return null;
  }
  const detail = payload.detail;
  if (typeof detail === "string") return detail;
  if (
    detail &&
    typeof detail === "object" &&
    "errors" in detail &&
    Array.isArray(detail.errors)
  ) {
    return detail.errors.filter((item): item is string => {
      return typeof item === "string";
    }).join("; ");
  }
  if (
    detail &&
    typeof detail === "object" &&
    "problems" in detail &&
    Array.isArray(detail.problems)
  ) {
    const message = "message" in detail && typeof detail.message === "string" ? detail.message : "";
    const problems = detail.problems.filter((item): item is string => typeof item === "string");
    return [message, ...problems].filter(Boolean).join("\n• ");
  }
  return null;
}

export interface CrawlRunSummary {
  crawl_run_id: string;
  connector: string;
  source: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  actor: string;
  ontology_version: string;
  crawler_version: string;
  settings_version: number | null;
  settings_hash: string | null;
  settings: Record<string, unknown>;
  counts: Record<string, number>;
  error: string | null;
  latest_evaluation: CrawlEvaluationSummary | null;
  /** Who asked for the crawl, when it was started through the API. */
  requested_by?: string | null;
  /** False when the crawl did not connect as the crawler machine user. */
  isolated?: boolean;
  /** User-provided label or note for this crawl. */
  note?: string | null;
}

/** One run of the Workbench crawl Job: a crawl that was asked for. */
export interface CrawlLaunch {
  job_run_id: string;
  status: string;
  active: boolean;
  created_at: string | null;
  finished_at: string | null;
  source_id: string | null;
  dataset_id: string | null;
  full: boolean;
  requested_by: string | null;
  note?: string | null;
}

export interface CrawlLaunches {
  available: boolean;
  reason: string | null;
  job_name?: string;
  crawler_identity?: string;
  launches: CrawlLaunch[];
}

export interface CrawlTarget {
  kind: "source" | "dataset";
  id: string;
  label: string;
  connector: string;
}

/** The headline numbers of an evaluation (CR-8); null where undefined (nothing to score). */
export type CrawlEvaluationNumbers = Record<string, number | null>;

export interface CrawlEvaluationSummary {
  evaluation_id: string;
  crawl_run_id: string;
  dataset_id: string;
  evaluated_at: string;
  evaluator: string;
  evaluator_mode: "proxy" | "workload_user" | string;
  harness_version: string;
  ontology_version: string;
  strategy: string;
  status: string;
  error: string | null;
  summary: CrawlEvaluationNumbers;
}

export interface CrawlEvaluation extends CrawlEvaluationSummary {
  metrics: Record<string, unknown>;
  run?: {
    started_at: string;
    finished_at: string | null;
    status: string;
    source: string;
    crawler_version: string;
    settings_version: number | null;
    ontology_version: string;
    counts: Record<string, number>;
  };
}

export interface CrawlRunAsset {
  asset_id: string;
  asset_version_id: string;
  ontology_class: string;
  mime_type: string;
  status: string;
  status_detail: string;
  size_bytes: number;
  semantic_timestamp: string | null;
  object_key: string | null;
}

export interface CrawlRunDetail extends CrawlRunSummary {
  asset_counts: { by_status: Record<string, number>; by_class: Record<string, number> };
  assets: CrawlRunAsset[];
}

export interface DataSourceType {
  connector: string;
  label: string;
  connection_help: string;
  scope_schema: Record<string, unknown>;
}

export interface CrawlSummary {
  crawl_run_id?: string;
  status: string;
  started_at: string;
  finished_at?: string | null;
  counts: Record<string, number>;
}

export interface DataSourceView {
  id: string;
  organization_id: string;
  name: string;
  connector: string;
  connection_ref: string;
  description: string;
  scope: Record<string, unknown>;
  crawl: { enabled?: boolean; settings_version?: number | null; schedule?: string };
  crawlable: boolean;
  updated_at: string | null;
  updated_by: string | null;
  last_crawl?: CrawlSummary | null;
  recent_crawls?: CrawlSummary[];
}

export interface DataSourceInput {
  name: string;
  connector: string;
  connection_ref: string;
  description: string;
  scope: Record<string, unknown>;
  crawl: Record<string, unknown>;
}

export interface DataSourceTestResult {
  ok: boolean;
  detail: string;
  sample: Array<{ asset_id: string; mime_type: string; size_bytes: number | null; semantic_timestamp: string | null }>;
  tested_as?: string;
}

export interface CrawlerSettingsVersion {
  version: number;
  content_hash: string;
  created_at: string;
  created_by: string;
  note: string;
  active: boolean;
}

export interface CrawlerSettingsState {
  active_version: number | null;
  using_defaults: boolean;
  content_hash: string;
  settings: Record<string, unknown>;
  versions: CrawlerSettingsVersion[];
}

export interface CrawlerSettingsSaveResult extends CrawlerSettingsVersion {
  created: boolean;
  warnings: string[];
}

export interface OntologyVersionSummary {
  version: string;
  content_hash: string;
  node_count: number;
  edge_count: number;
  enum_count: number;
  is_active: boolean;
  /** Recorded in helios_index (O-6); false for versions only in the local cache. */
  in_lakehouse?: boolean;
  published_at?: string | null;
  published_by?: string | null;
  schema_path?: string | null;
}

export interface OntologySchema {
  schema_path: string;
  name: string | null;
  title: string | null;
  version: string;
  layer: "core" | "pack" | "customer";
}

export interface OntologyCheckResult {
  version: string;
  content_hash: string;
  node_count: number;
  edge_count: number;
  enum_count: number;
  class_count: number;
  schema_path: string;
  broken_mappings: Array<Record<string, string>>;
  /** new; identical (publishing is a no-op); conflict (the version exists with other content). */
  status: "new" | "identical" | "conflict";
  existing_content_hash: string | null;
  changes: {
    compared_with: string;
    classes_added: string[];
    classes_removed: string[];
    mappings_added: string[];
    mappings_removed: string[];
    attributes_added: number;
    attributes_removed: number;
  } | null;
  lakehouse: boolean;
}

export interface OntologyPublishResult {
  version: string;
  content_hash: string;
  node_count: number;
  edge_count: number;
  enum_count: number;
  broken_mappings: Array<Record<string, string>>;
  recorded_in_lakehouse: boolean;
}

export interface OntologyGraphPayload {
  version: string;
  content_hash: string;
  nodes: Array<{ label: string; key: string; properties: Record<string, unknown> }>;
  edges: Array<{
    type: string;
    from_label: string;
    from_key: string;
    to_label: string;
    to_key: string;
    properties: Record<string, unknown>;
  }>;
}

export interface OntologyClassDetail {
  class: { name: string; properties: Record<string, unknown> };
  parents: string[];
  attributes: Array<{ name: string; properties: Record<string, unknown> }>;
  ranges: Array<{ attribute: string; range_class: string }>;
}

export interface HeliosApi {
  health(): Promise<ApiHealth>;
  diagnostics(): Promise<ApiDiagnostics>;
  ontologyVersions?(): Promise<OntologyVersionSummary[]>;
  ontologySchemas?(): Promise<OntologySchema[]>;
  checkOntology?(version: string, schemaPath: string, organizationId: string): Promise<OntologyCheckResult>;
  publishOntology?(version: string, schemaPath: string, organizationId: string): Promise<OntologyPublishResult>;
  activateOntology?(version: string, organizationId: string): Promise<{ version: string }>;
  ontologyGraph?(version: string): Promise<OntologyGraphPayload>;
  ontologyClass?(version: string, className: string): Promise<OntologyClassDetail>;
  crawlRuns?(source?: string): Promise<CrawlRunSummary[]>;
  crawlRun?(crawlRunId: string): Promise<CrawlRunDetail>;
  startCrawl?(target: CrawlTarget, full: boolean, note?: string): Promise<CrawlLaunch>;
  crawlLaunches?(): Promise<CrawlLaunches>;
  crawlTargets?(): Promise<CrawlTarget[]>;
  evaluateCrawlRun?(crawlRunId: string, datasetId: string): Promise<CrawlEvaluation>;
  crawlRunEvaluations?(crawlRunId: string): Promise<CrawlEvaluation[]>;
  crawlerEvaluations?(dataset?: string): Promise<CrawlEvaluation[]>;
  crawlerSettings?(): Promise<CrawlerSettingsState>;
  crawlerSettingsDefaults?(): Promise<{ content_hash: string; settings: Record<string, unknown> }>;
  crawlerSettingsVersion?(
    version: number,
  ): Promise<CrawlerSettingsVersion & { settings: Record<string, unknown> }>;
  saveCrawlerSettings?(
    settings: Record<string, unknown>,
    note: string,
  ): Promise<CrawlerSettingsSaveResult>;
  activateCrawlerSettings?(version: number): Promise<{ version: number }>;
  dataSourceTypes?(): Promise<DataSourceType[]>;
  dataSources?(organizationId: string): Promise<DataSourceView[]>;
  dataSource?(organizationId: string, id: string): Promise<DataSourceView>;
  createDataSource?(organizationId: string, input: DataSourceInput): Promise<DataSourceView>;
  updateDataSource?(organizationId: string, id: string, input: DataSourceInput): Promise<DataSourceView>;
  deleteDataSource?(organizationId: string, id: string): Promise<{ deleted: string }>;
  testDataSource?(organizationId: string, id: string): Promise<DataSourceTestResult>;
  modelProviderSettings?(): Promise<ModelProviderSettings>;
  updateModelProviderSettings?(
    settings: ModelProviderSettingsWrite,
  ): Promise<ModelProviderSettings>;
  deleteModelProviderSettings?(): Promise<ModelProviderSettings>;
  mcpSettings?(): Promise<MCPSettings>;
  updateMcpSettings?(maxToolRounds: number): Promise<MCPSettings>;
  deleteMcpSettings?(): Promise<MCPSettings>;
  mcpStatus?(modelId: string): Promise<MCPStatus>;
  modelTraces?(
    modelId: string,
    options?: {
      purpose?: "conversation" | "evaluation";
      status?: TraceRun["status"];
      includeAll?: boolean;
    },
  ): Promise<TraceCollection>;
  modelTrace?(modelId: string, runId: string): Promise<TraceDetail>;
  modelEvaluations?(modelId: string): Promise<EvaluationCollection>;
  modelEvaluation?(modelId: string, runId: string): Promise<EvaluationRun>;
  createModelEvaluation?(
    modelId: string,
    suiteId?: string,
    repetitions?: number,
  ): Promise<EvaluationRun>;
  cancelModelEvaluation?(
    modelId: string,
    runId: string,
  ): Promise<EvaluationRun>;
  organizations(): Promise<OrganizationsResponse>;
  models(organizationId: string): Promise<ModelsResponse>;
  modelOverview(modelId: string): Promise<ModelOverview>;
  modelRuns?(modelId: string): Promise<DiscoveryRunsResponse>;
  modelRun?(modelId: string, runId: string): Promise<DiscoveryRun>;
  modelRunProfileSummary?(
    modelId: string,
    runId: string,
  ): Promise<HistoricalProfileSummary>;
  modelRunTableProfile?(
    modelId: string,
    runId: string,
    tableId: string,
  ): Promise<HistoricalTableProfile>;
  modelRunProposals?(
    modelId: string,
    runId: string,
    options: ProposalCollectionOptions,
  ): Promise<ProposalCollection>;
  modelStatus(
    modelId: string,
    includeDetails?: boolean,
  ): Promise<ModelSystemStatus>;
  modelGraph(
    modelId: string,
    options?: GraphNavigationOptions,
  ): Promise<HeliosGraphDto>;
  modelGraphDetail(
    modelId: string,
    elementId: string,
    reviewRunId?: string,
  ): Promise<GraphElementDetail>;
  decideModelProposal(
    modelId: string,
    runId: string,
    decision: ReviewDecisionRequest,
  ): Promise<ReviewDecisionResponse>;
  modelReview(modelId: string, runId: string): Promise<ReviewSummary>;
  decideModelDataset(
    modelId: string,
    runId: string,
    table: string,
    decision: "accept" | "reject",
  ): Promise<ReviewMutationResponse>;
  bulkAcceptModelProposals(
    modelId: string,
    runId: string,
    minConfidence: number,
  ): Promise<ReviewMutationResponse>;
  resetModelReview(
    modelId: string,
    runId: string,
    section?: ReviewSection,
  ): Promise<ReviewMutationResponse>;
  publishModelReview(
    modelId: string,
    runId: string,
  ): Promise<ReviewPublicationResponse>;
  modelGlossary?(modelId: string): Promise<ModelGlossary>;
  createModelGlossary?(
    modelId: string,
    body: { name: string; description: string },
  ): Promise<ModelGlossary>;
  deleteModelGlossary?(modelId: string): Promise<{ ok: true }>;
  modelGlossaryTerms?(
    modelId: string,
    options?: GlossaryTermsOptions,
  ): Promise<GlossaryTermsResponse>;
  modelGlossaryTerm?(
    modelId: string,
    termId: string,
  ): Promise<GlossaryTermResponse>;
  createModelGlossaryTerm?(
    modelId: string,
    body: GlossaryTermWrite,
  ): Promise<GlossaryTermResponse>;
  updateModelGlossaryTerm?(
    modelId: string,
    termId: string,
    body: GlossaryTermWrite,
  ): Promise<GlossaryTermResponse>;
  deleteModelGlossaryTerm?(
    modelId: string,
    termId: string,
  ): Promise<{ ok: true }>;
  importModelGlossary?(
    modelId: string,
    file: File,
  ): Promise<GlossaryImportResult>;
  modelGlossaryAssignableAssets?(
    modelId: string,
    query?: string,
  ): Promise<{ items: GlossaryAssignableAsset[] }>;
  assignModelGlossaryTerm?(
    modelId: string,
    termId: string,
    canvasElementId: string,
  ): Promise<{ ok: true }>;
  unassignModelGlossaryTerm?(
    modelId: string,
    termId: string,
    assignmentId: string,
  ): Promise<{ ok: true }>;
  createConversationTurn?(
    modelId: string,
    message: string,
  ): Promise<ConversationTurn>;
  modelConversations?(
    modelId: string,
  ): Promise<ConversationCollection>;
  createModelConversation?(
    modelId: string,
    message: string,
  ): Promise<PersistedConversationTurn>;
  modelConversation?(
    modelId: string,
    conversationId: string,
  ): Promise<ConversationDetail>;
  appendModelConversationTurn?(
    modelId: string,
    conversationId: string,
    message: string,
    expectedVersion: number,
  ): Promise<PersistedConversationTurn>;
  archiveModelConversation?(
    modelId: string,
    conversationId: string,
    archived?: boolean,
  ): Promise<ConversationDetail>;
  auditEvents?(
    options?: AuditEventOptions,
  ): Promise<AuditEventCollection>;
  auditEvent?(eventId: string): Promise<AuditEvent>;
  auditSessions?(
    organizationId?: string,
    includeAll?: boolean,
  ): Promise<AuditSessionCollection>;
  recordClientAuditEvent?(
    event: ClientAuditEvent,
  ): Promise<{ ok: true }>;
  assistantWorkspaceState?(
    organizationId: string,
    modelId?: string,
  ): Promise<AssistantWorkspaceState>;
  assistantTurn?(body: AssistantTurnRequest): Promise<AssistantTurnResponse>;
  applicationUrl?(path: string): string;
}

const SESSION_STORAGE_KEY = "helios.audit.session";
let fallbackSessionId: string | undefined;

function auditSessionId(): string {
  try {
    const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existing) return existing;
    const created = window.crypto?.randomUUID?.()
      ?? `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, created);
    return created;
  } catch {
    fallbackSessionId ??=
      `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return fallbackSessionId;
  }
}

function configuredApiUrl(): string {
  const value =
    window.__HELIOS_CONFIG__?.apiUrl?.trim() ||
    import.meta.env.VITE_HELIOS_API_URL?.trim();
  if (!value) {
    throw new ApiUnavailableError(
      "The Helios API URL has not been configured.",
    );
  }

  try {
    const url = new URL(value);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    ) {
      throw new Error("API URL must be an HTTP(S) origin.");
    }
    return url.origin;
  } catch (error) {
    throw new ApiUnavailableError(
      "The configured Helios API URL is invalid.",
      error,
    );
  }
}

export class HeliosApiClient implements HeliosApi {
  constructor(private readonly baseUrl = configuredApiUrl()) {}

  get apiUrl(): string {
    return this.baseUrl;
  }

  health(): Promise<ApiHealth> {
    return this.get<ApiHealth>("/api/v1/healthz");
  }

  diagnostics(): Promise<ApiDiagnostics> {
    return this.get<ApiDiagnostics>("/api/v1/diagnostics");
  }

  modelProviderSettings(): Promise<ModelProviderSettings> {
    return this.get<ModelProviderSettings>("/api/v1/model-provider-settings");
  }

  ontologyVersions(): Promise<OntologyVersionSummary[]> {
    return this.get<OntologyVersionSummary[]>("/api/v1/ontology/versions");
  }
  ontologySchemas(): Promise<OntologySchema[]> {
    return this.get<OntologySchema[]>("/api/v1/ontology/schemas");
  }

  checkOntology(version: string, schemaPath: string, organizationId: string): Promise<OntologyCheckResult> {
    return this.post<OntologyCheckResult>(
      `/api/v1/ontology:check?organization_id=${encodeURIComponent(organizationId)}`,
      { version, schema_path: schemaPath },
    );
  }

  publishOntology(version: string, schemaPath: string, organizationId: string): Promise<OntologyPublishResult> {
    return this.post<OntologyPublishResult>(
      `/api/v1/ontology:publish?organization_id=${encodeURIComponent(organizationId)}`,
      { version, schema_path: schemaPath },
    );
  }

  activateOntology(version: string, organizationId: string): Promise<{ version: string }> {
    return this.post<{ version: string }>(
      `/api/v1/ontology/${encodeURIComponent(version)}:activate?organization_id=${encodeURIComponent(organizationId)}`,
    );
  }


  ontologyGraph(version: string): Promise<OntologyGraphPayload> {
    return this.get<OntologyGraphPayload>(
      `/api/v1/ontology/${encodeURIComponent(version)}/graph`,
    );
  }

  ontologyClass(version: string, className: string): Promise<OntologyClassDetail> {
    return this.get<OntologyClassDetail>(
      `/api/v1/ontology/${encodeURIComponent(version)}/classes/${encodeURIComponent(className)}`,
    );
  }
  crawlRuns(source?: string): Promise<CrawlRunSummary[]> {
    const query = source ? `?source=${encodeURIComponent(source)}` : "";
    return this.get<CrawlRunSummary[]>(`/api/v1/crawler/runs${query}`);
  }

  crawlRun(crawlRunId: string): Promise<CrawlRunDetail> {
    return this.get<CrawlRunDetail>(`/api/v1/crawler/runs/${encodeURIComponent(crawlRunId)}`);
  }

  startCrawl(target: CrawlTarget, full: boolean, note?: string): Promise<CrawlLaunch> {
    return this.post<CrawlLaunch>("/api/v1/crawler/runs", {
      ...(target.kind === "source" ? { source_id: target.id } : { dataset_id: target.id }),
      full,
      ...(note ? { note } : {}),
    });
  }

  crawlLaunches(): Promise<CrawlLaunches> {
    return this.get<CrawlLaunches>("/api/v1/crawler/launches");
  }

  crawlTargets(): Promise<CrawlTarget[]> {
    return this.get<CrawlTarget[]>("/api/v1/crawler/targets");
  }

  evaluateCrawlRun(crawlRunId: string, datasetId: string): Promise<CrawlEvaluation> {
    return this.post<CrawlEvaluation>(
      `/api/v1/crawler/runs/${encodeURIComponent(crawlRunId)}:evaluate`,
      { dataset_id: datasetId },
    );
  }

  crawlRunEvaluations(crawlRunId: string): Promise<CrawlEvaluation[]> {
    return this.get<CrawlEvaluation[]>(
      `/api/v1/crawler/runs/${encodeURIComponent(crawlRunId)}/evaluations`,
    );
  }

  crawlerEvaluations(dataset?: string): Promise<CrawlEvaluation[]> {
    const query = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
    return this.get<CrawlEvaluation[]>(`/api/v1/crawler/evaluations${query}`);
  }

  crawlerSettings(): Promise<CrawlerSettingsState> {
    return this.get<CrawlerSettingsState>("/api/v1/crawler/settings");
  }

  crawlerSettingsDefaults(): Promise<{ content_hash: string; settings: Record<string, unknown> }> {
    return this.get("/api/v1/crawler/settings/defaults");
  }

  crawlerSettingsVersion(
    version: number,
  ): Promise<CrawlerSettingsVersion & { settings: Record<string, unknown> }> {
    return this.get(`/api/v1/crawler/settings/versions/${version}`);
  }

  saveCrawlerSettings(
    settings: Record<string, unknown>,
    note: string,
  ): Promise<CrawlerSettingsSaveResult> {
    return this.post<CrawlerSettingsSaveResult>("/api/v1/crawler/settings", { settings, note });
  }

  activateCrawlerSettings(version: number): Promise<{ version: number }> {
    return this.post<{ version: number }>(`/api/v1/crawler/settings/${version}:activate`);
  }
  dataSourceTypes(): Promise<DataSourceType[]> {
    return this.get<DataSourceType[]>("/api/v1/data-source-types");
  }

  dataSources(organizationId: string): Promise<DataSourceView[]> {
    return this.get<DataSourceView[]>(`/api/v1/organizations/${encodeURIComponent(organizationId)}/data-sources`);
  }

  dataSource(organizationId: string, id: string): Promise<DataSourceView> {
    return this.get<DataSourceView>(`/api/v1/organizations/${encodeURIComponent(organizationId)}/data-sources/${encodeURIComponent(id)}`);
  }

  createDataSource(organizationId: string, input: DataSourceInput): Promise<DataSourceView> {
    return this.post<DataSourceView>(`/api/v1/organizations/${encodeURIComponent(organizationId)}/data-sources`, input);
  }

  updateDataSource(organizationId: string, id: string, input: DataSourceInput): Promise<DataSourceView> {
    return this.put<DataSourceView>(`/api/v1/organizations/${encodeURIComponent(organizationId)}/data-sources/${encodeURIComponent(id)}`, input);
  }

  deleteDataSource(organizationId: string, id: string): Promise<{ deleted: string }> {
    return this.delete<{ deleted: string }>(`/api/v1/organizations/${encodeURIComponent(organizationId)}/data-sources/${encodeURIComponent(id)}`);
  }

  testDataSource(organizationId: string, id: string): Promise<DataSourceTestResult> {
    return this.post<DataSourceTestResult>(`/api/v1/organizations/${encodeURIComponent(organizationId)}/data-sources/${encodeURIComponent(id)}:test`);
  }



  updateModelProviderSettings(
    settings: ModelProviderSettingsWrite,
  ): Promise<ModelProviderSettings> {
    return this.put<ModelProviderSettings>(
      "/api/v1/model-provider-settings",
      settings,
    );
  }

  deleteModelProviderSettings(): Promise<ModelProviderSettings> {
    return this.delete<ModelProviderSettings>(
      "/api/v1/model-provider-settings",
    );
  }

  mcpSettings(): Promise<MCPSettings> {
    return this.get<MCPSettings>("/api/v1/mcp-settings");
  }

  updateMcpSettings(maxToolRounds: number): Promise<MCPSettings> {
    return this.put<MCPSettings>(
      "/api/v1/mcp-settings",
      { max_tool_rounds: maxToolRounds },
    );
  }

  deleteMcpSettings(): Promise<MCPSettings> {
    return this.delete<MCPSettings>("/api/v1/mcp-settings");
  }

  mcpStatus(modelId: string): Promise<MCPStatus> {
    return this.get<MCPStatus>(
      `/api/v1/models/${encodeURIComponent(modelId)}/mcp-status`,
    );
  }

  modelTraces(
    modelId: string,
    options: {
      purpose?: "conversation" | "evaluation";
      status?: TraceRun["status"];
      includeAll?: boolean;
    } = {},
  ): Promise<TraceCollection> {
    const query = new URLSearchParams();
    if (options.purpose) query.set("purpose", options.purpose);
    if (options.status) query.set("status", options.status);
    if (options.includeAll) query.set("include_all", "true");
    const suffix = query.size ? `?${query}` : "";
    return this.get<TraceCollection>(
      `/api/v1/models/${encodeURIComponent(modelId)}/traces${suffix}`,
    );
  }

  modelTrace(modelId: string, runId: string): Promise<TraceDetail> {
    return this.get<TraceDetail>(
      `/api/v1/models/${encodeURIComponent(modelId)}/traces/${encodeURIComponent(runId)}`,
    );
  }

  modelEvaluations(modelId: string): Promise<EvaluationCollection> {
    return this.get<EvaluationCollection>(
      `/api/v1/models/${encodeURIComponent(modelId)}/evaluations`,
    );
  }

  modelEvaluation(modelId: string, runId: string): Promise<EvaluationRun> {
    return this.get<EvaluationRun>(
      `/api/v1/models/${encodeURIComponent(modelId)}/evaluations/${encodeURIComponent(runId)}`,
    );
  }

  createModelEvaluation(
    modelId: string,
    suiteId = "tpcds",
    repetitions = 3,
  ): Promise<EvaluationRun> {
    return this.post<EvaluationRun>(
      `/api/v1/models/${encodeURIComponent(modelId)}/evaluations`,
      { suite_id: suiteId, repetitions },
    );
  }

  cancelModelEvaluation(
    modelId: string,
    runId: string,
  ): Promise<EvaluationRun> {
    return this.post<EvaluationRun>(
      `/api/v1/models/${encodeURIComponent(modelId)}/evaluations/${encodeURIComponent(runId)}/cancel`,
      undefined,
    );
  }

  organizations(): Promise<OrganizationsResponse> {
    return this.get<OrganizationsResponse>("/api/v1/organizations");
  }

  models(organizationId: string): Promise<ModelsResponse> {
    const query = new URLSearchParams({ organization_id: organizationId });
    return this.get<ModelsResponse>(`/api/v1/models?${query}`);
  }

  modelOverview(modelId: string): Promise<ModelOverview> {
    return this.get<ModelOverview>(
      `/api/v1/models/${encodeURIComponent(modelId)}/overview`,
    );
  }

  modelRuns(modelId: string): Promise<DiscoveryRunsResponse> {
    return this.get<DiscoveryRunsResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/runs`,
    );
  }

  modelRun(modelId: string, runId: string): Promise<DiscoveryRun> {
    return this.get<DiscoveryRun>(
      `/api/v1/models/${encodeURIComponent(modelId)}/runs/${encodeURIComponent(runId)}`,
    );
  }

  modelRunProfileSummary(
    modelId: string,
    runId: string,
  ): Promise<HistoricalProfileSummary> {
    return this.get<HistoricalProfileSummary>(
      `/api/v1/models/${encodeURIComponent(modelId)}/runs/${encodeURIComponent(runId)}/profile`,
    );
  }

  modelRunTableProfile(
    modelId: string,
    runId: string,
    tableId: string,
  ): Promise<HistoricalTableProfile> {
    return this.get<HistoricalTableProfile>(
      `/api/v1/models/${encodeURIComponent(modelId)}/runs/${encodeURIComponent(runId)}/profile/tables/${encodeURIComponent(tableId)}`,
    );
  }

  modelRunProposals(
    modelId: string,
    runId: string,
    options: ProposalCollectionOptions,
  ): Promise<ProposalCollection> {
    const query = new URLSearchParams({ section: options.section });
    if (options.decision) query.set("decision", options.decision);
    if (options.query?.trim()) query.set("query", options.query.trim());
    if (options.offset !== undefined) query.set("offset", String(options.offset));
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    return this.get<ProposalCollection>(
      `/api/v1/models/${encodeURIComponent(modelId)}/runs/${encodeURIComponent(runId)}/proposals?${query}`,
    );
  }

  modelStatus(
    modelId: string,
    includeDetails = false,
  ): Promise<ModelSystemStatus> {
    const query = includeDetails ? "?details=true" : "";
    return this.get<ModelSystemStatus>(
      `/api/v1/models/${encodeURIComponent(modelId)}/status${query}`,
    );
  }

  modelGraph(
    modelId: string,
    options: GraphNavigationOptions = {},
  ): Promise<HeliosGraphDto> {
    const query = new URLSearchParams();
    if (options.navigation) query.set("navigation", "true");
    if (options.lens) query.set("lens", options.lens);
    if (options.focusNodeId) {
      query.set("focus_node_id", options.focusNodeId);
    }
    if (options.depth !== undefined) {
      query.set("depth", String(options.depth));
    }
    if (options.includeAttributes) {
      query.set("include_attributes", "true");
    }
    if (options.limit !== undefined) {
      query.set("limit", String(options.limit));
    }
    if (options.query) query.set("query", options.query);
    if (options.reviewRunId) {
      query.set("review_run_id", options.reviewRunId);
    }
    const suffix = query.size ? `?${query}` : "";
    return this.get<HeliosGraphDto>(
      `/api/v1/models/${encodeURIComponent(modelId)}/graph${suffix}`,
    );
  }

  modelGraphDetail(
    modelId: string,
    elementId: string,
    reviewRunId?: string,
  ): Promise<GraphElementDetail> {
    const query = new URLSearchParams({ element_id: elementId });
    if (reviewRunId) query.set("review_run_id", reviewRunId);
    return this.get<GraphElementDetail>(
      `/api/v1/models/${encodeURIComponent(modelId)}/graph/detail?${query}`,
    );
  }

  decideModelProposal(
    modelId: string,
    runId: string,
    decision: ReviewDecisionRequest,
  ): Promise<ReviewDecisionResponse> {
    return this.post<ReviewDecisionResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/reviews/${encodeURIComponent(runId)}/decisions`,
      decision,
    );
  }

  modelReview(modelId: string, runId: string): Promise<ReviewSummary> {
    return this.get<ReviewSummary>(
      `/api/v1/models/${encodeURIComponent(modelId)}/reviews/${encodeURIComponent(runId)}`,
    );
  }

  decideModelDataset(
    modelId: string,
    runId: string,
    table: string,
    decision: "accept" | "reject",
  ): Promise<ReviewMutationResponse> {
    return this.post<ReviewMutationResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/reviews/${encodeURIComponent(runId)}/decisions/dataset`,
      { table, decision },
    );
  }

  bulkAcceptModelProposals(
    modelId: string,
    runId: string,
    minConfidence: number,
  ): Promise<ReviewMutationResponse> {
    return this.post<ReviewMutationResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/reviews/${encodeURIComponent(runId)}/decisions/bulk`,
      { min_confidence: minConfidence },
    );
  }

  resetModelReview(
    modelId: string,
    runId: string,
    section?: ReviewSection,
  ): Promise<ReviewMutationResponse> {
    return this.post<ReviewMutationResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/reviews/${encodeURIComponent(runId)}/reset`,
      { section: section ?? null },
    );
  }

  publishModelReview(
    modelId: string,
    runId: string,
  ): Promise<ReviewPublicationResponse> {
    return this.post<ReviewPublicationResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/reviews/${encodeURIComponent(runId)}/publish`,
      undefined,
    );
  }

  modelGlossary(modelId: string): Promise<ModelGlossary> {
    return this.get<ModelGlossary>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary`,
    );
  }

  createModelGlossary(
    modelId: string,
    body: { name: string; description: string },
  ): Promise<ModelGlossary> {
    return this.post<ModelGlossary>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary`,
      body,
    );
  }

  deleteModelGlossary(modelId: string): Promise<{ ok: true }> {
    return this.delete<{ ok: true }>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary?confirm=true`,
    );
  }

  modelGlossaryTerms(
    modelId: string,
    options: GlossaryTermsOptions = {},
  ): Promise<GlossaryTermsResponse> {
    const query = new URLSearchParams();
    if (options.query) query.set("query", options.query);
    if (options.status) query.set("status", options.status);
    if (options.sort) query.set("sort", options.sort);
    if (options.direction) query.set("direction", options.direction);
    if (options.offset !== undefined) query.set("offset", String(options.offset));
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size ? `?${query}` : "";
    return this.get<GlossaryTermsResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms${suffix}`,
    );
  }

  modelGlossaryTerm(
    modelId: string,
    termId: string,
  ): Promise<GlossaryTermResponse> {
    return this.get<GlossaryTermResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms/${encodeURIComponent(termId)}`,
    );
  }

  createModelGlossaryTerm(
    modelId: string,
    body: GlossaryTermWrite,
  ): Promise<GlossaryTermResponse> {
    return this.post<GlossaryTermResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms`,
      body,
    );
  }

  updateModelGlossaryTerm(
    modelId: string,
    termId: string,
    body: GlossaryTermWrite,
  ): Promise<GlossaryTermResponse> {
    return this.patch<GlossaryTermResponse>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms/${encodeURIComponent(termId)}`,
      body,
    );
  }

  deleteModelGlossaryTerm(
    modelId: string,
    termId: string,
  ): Promise<{ ok: true }> {
    return this.delete<{ ok: true }>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms/${encodeURIComponent(termId)}`,
    );
  }

  importModelGlossary(
    modelId: string,
    file: File,
  ): Promise<GlossaryImportResult> {
    const body = new FormData();
    body.set("file", file);
    return this.request<GlossaryImportResult>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/import`,
      { method: "POST", body },
    );
  }

  modelGlossaryAssignableAssets(
    modelId: string,
    query = "",
  ): Promise<{ items: GlossaryAssignableAsset[] }> {
    const suffix = query.trim()
      ? `?${new URLSearchParams({ query: query.trim() })}`
      : "";
    return this.get<{ items: GlossaryAssignableAsset[] }>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/assignable-assets${suffix}`,
    );
  }

  assignModelGlossaryTerm(
    modelId: string,
    termId: string,
    canvasElementId: string,
  ): Promise<{ ok: true }> {
    return this.post<{ ok: true }>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms/${encodeURIComponent(termId)}/assignments`,
      { canvas_element_id: canvasElementId },
    );
  }

  unassignModelGlossaryTerm(
    modelId: string,
    termId: string,
    assignmentId: string,
  ): Promise<{ ok: true }> {
    return this.delete<{ ok: true }>(
      `/api/v1/models/${encodeURIComponent(modelId)}/glossary/terms/${encodeURIComponent(termId)}/assignments/${encodeURIComponent(assignmentId)}`,
    );
  }

  createConversationTurn(
    modelId: string,
    message: string,
  ): Promise<ConversationTurn> {
    return this.post<ConversationTurn>(
      `/api/v1/models/${encodeURIComponent(modelId)}/conversation/turns`,
      { message },
    );
  }

  modelConversations(modelId: string): Promise<ConversationCollection> {
    return this.get<ConversationCollection>(
      `/api/v1/models/${encodeURIComponent(modelId)}/conversations`,
    );
  }

  createModelConversation(
    modelId: string,
    message: string,
  ): Promise<PersistedConversationTurn> {
    return this.post<PersistedConversationTurn>(
      `/api/v1/models/${encodeURIComponent(modelId)}/conversations`,
      { message },
    );
  }

  modelConversation(
    modelId: string,
    conversationId: string,
  ): Promise<ConversationDetail> {
    return this.get<ConversationDetail>(
      `/api/v1/models/${encodeURIComponent(modelId)}/conversations/${encodeURIComponent(conversationId)}`,
    );
  }

  appendModelConversationTurn(
    modelId: string,
    conversationId: string,
    message: string,
    expectedVersion: number,
  ): Promise<PersistedConversationTurn> {
    return this.post<PersistedConversationTurn>(
      `/api/v1/models/${encodeURIComponent(modelId)}/conversations/${encodeURIComponent(conversationId)}/turns`,
      { message, expected_version: expectedVersion },
    );
  }

  archiveModelConversation(
    modelId: string,
    conversationId: string,
    archived = true,
  ): Promise<ConversationDetail> {
    return this.patch<ConversationDetail>(
      `/api/v1/models/${encodeURIComponent(modelId)}/conversations/${encodeURIComponent(conversationId)}`,
      { archived },
    );
  }

  auditEvents(
    options: AuditEventOptions = {},
  ): Promise<AuditEventCollection> {
    const query = new URLSearchParams();
    if (options.organizationId) {
      query.set("organization_id", options.organizationId);
    }
    if (options.principalId) query.set("principal_id", options.principalId);
    if (options.sessionId) query.set("session_id", options.sessionId);
    if (options.modelId) query.set("model_id", options.modelId);
    if (options.component) query.set("component", options.component);
    if (options.eventType) query.set("event_type", options.eventType);
    if (options.outcome) query.set("outcome", options.outcome);
    if (options.severity) query.set("severity", options.severity);
    if (options.includeAll) query.set("include_all", "true");
    if (options.offset != null) query.set("offset", String(options.offset));
    if (options.limit != null) query.set("limit", String(options.limit));
    const suffix = query.toString() ? `?${query}` : "";
    return this.get<AuditEventCollection>(`/api/v1/audit/events${suffix}`);
  }

  auditEvent(eventId: string): Promise<AuditEvent> {
    return this.get<AuditEvent>(
      `/api/v1/audit/events/${encodeURIComponent(eventId)}`,
    );
  }

  auditSessions(
    organizationId?: string,
    includeAll = false,
  ): Promise<AuditSessionCollection> {
    const query = new URLSearchParams();
    if (organizationId) query.set("organization_id", organizationId);
    if (includeAll) query.set("include_all", "true");
    const suffix = query.toString() ? `?${query}` : "";
    return this.get<AuditSessionCollection>(
      `/api/v1/audit/sessions${suffix}`,
    );
  }

  recordClientAuditEvent(
    event: ClientAuditEvent,
  ): Promise<{ ok: true }> {
    return this.post<{ ok: true }>("/api/v1/audit/client-events", event);
  }

  assistantWorkspaceState(
    organizationId: string,
    modelId?: string,
  ): Promise<AssistantWorkspaceState> {
    const query = new URLSearchParams({ organization: organizationId });
    if (modelId) query.set("model", modelId);
    return this.get<AssistantWorkspaceState>(
      `/api/v1/assistant/workspace-state?${query.toString()}`,
    );
  }

  assistantTurn(body: AssistantTurnRequest): Promise<AssistantTurnResponse> {
    return this.post<AssistantTurnResponse>("/api/v1/assistant/turn", body);
  }

  applicationUrl(path: string): string {
    return new URL(path, this.baseUrl).toString();
  }

  private async get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "GET" });
  }

  private async post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
  }

  private async patch<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  private async put<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  private async delete<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "DELETE" });
  }

  private async request<T>(
    path: string,
    init: RequestInit,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        mode: "cors",
        credentials: "include",
        ...init,
        headers: {
          Accept: "application/json",
          "X-Helios-Session-ID": auditSessionId(),
          ...init.headers,
        },
      });
    } catch (error) {
      throw new ApiUnavailableError("The Helios API is unavailable.", error);
    }

    if (response.status === 401 || response.redirected) {
      throw new AuthenticationError("Authentication with Helios failed.");
    }
    if (response.status === 403) {
      throw new AuthorizationError(
        "You do not have access to Helios resources.",
      );
    }
    if (response.status === 409) {
      let detail: unknown;
      try {
        detail = await response.clone().json();
      } catch {
        detail = null;
      }
      throw new ConflictError(
        apiErrorMessage(detail) || "The resource changed. Reload and retry.",
      );
    }
    if (!response.ok) {
      let detail: unknown;
      try {
        detail = await response.clone().json();
      } catch {
        detail = null;
      }
      const message = apiErrorMessage(detail);
      const text = message || `The Helios API returned HTTP ${response.status}.`;
      if (response.status === 503) {
        throw new ServiceUnavailableError(text);
      }
      throw new ApiUnavailableError(text);
    }
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new AuthenticationError(
        "The Helios API did not return an authenticated JSON response.",
      );
    }

    return (await response.json()) as T;
  }
}
