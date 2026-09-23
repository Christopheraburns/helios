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

export class AuthenticationError extends Error {}
export class AuthorizationError extends Error {}

export class ApiUnavailableError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
  }
}

export interface HeliosApi {
  health(): Promise<ApiHealth>;
  diagnostics(): Promise<ApiDiagnostics>;
  organizations(): Promise<OrganizationsResponse>;
  models(organizationId: string): Promise<ModelsResponse>;
  modelOverview(modelId: string): Promise<ModelOverview>;
  modelGraph(
    modelId: string,
    options?: GraphNavigationOptions,
  ): Promise<HeliosGraphDto>;
  modelGraphDetail(
    modelId: string,
    elementId: string,
  ): Promise<GraphElementDetail>;
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
    const suffix = query.size ? `?${query}` : "";
    return this.get<HeliosGraphDto>(
      `/api/v1/models/${encodeURIComponent(modelId)}/graph${suffix}`,
    );
  }

  modelGraphDetail(
    modelId: string,
    elementId: string,
  ): Promise<GraphElementDetail> {
    const query = new URLSearchParams({ element_id: elementId });
    return this.get<GraphElementDetail>(
      `/api/v1/models/${encodeURIComponent(modelId)}/graph/detail?${query}`,
    );
  }

  applicationUrl(path: string): string {
    return new URL(path, this.baseUrl).toString();
  }

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: "GET",
        mode: "cors",
        credentials: "include",
        headers: { Accept: "application/json" },
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
      throw new ApiUnavailableError(
        `The Helios API returned HTTP ${response.status}.`,
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
