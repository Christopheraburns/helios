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

export interface ModelsResponse {
  organization_id: string | null;
  models: ModelSummary[];
  count: number;
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
