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

export class AuthenticationError extends Error {}
export class AuthorizationError extends Error {}

export class ApiUnavailableError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
  }
}

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
  return null;
}

export interface HeliosApi {
  health(): Promise<ApiHealth>;
  diagnostics(): Promise<ApiDiagnostics>;
  organizations(): Promise<OrganizationsResponse>;
  models(organizationId: string): Promise<ModelsResponse>;
  modelOverview(modelId: string): Promise<ModelOverview>;
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
  applicationUrl?(path: string): string;
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
    if (!response.ok) {
      let detail: unknown;
      try {
        detail = await response.clone().json();
      } catch {
        detail = null;
      }
      const message = apiErrorMessage(detail);
      throw new ApiUnavailableError(
        message || `The Helios API returned HTTP ${response.status}.`,
      );
    }
    if (!response.headers.get("content-type")?.includes("application/json")) {
      throw new AuthenticationError(
        "The Helios API did not return an authenticated JSON response.",
      );
    }

    return (await response.json()) as T;
  }
}
