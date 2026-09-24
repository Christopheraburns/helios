import { useCallback, useEffect, useRef, useState } from "react";
import {
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import {
  ApiDiagnostics,
  ApiUnavailableError,
  AuthenticationError,
  AuthorizationError,
  HeliosApi,
  HeliosApiClient,
  HeliosGraphDto,
  GraphNavigationOptions,
  GraphElementDetail,
  ReviewDecisionRequest,
  ReviewDecisionResponse,
  ReviewMutationResponse,
  ReviewPublicationResponse,
  ReviewSection,
  ReviewSummary,
  ModelOverview,
  ModelSystemStatus,
  ModelSummary,
  OrganizationSummary,
} from "../api/client";

export type LoadStatus = "idle" | "loading" | "ready" | "error";
export type ApplicationErrorKind =
  | "authentication"
  | "authorization"
  | "unavailable";

export interface ApplicationContextState {
  status: LoadStatus;
  modelStatus: LoadStatus;
  overviewStatus: LoadStatus;
  errorKind?: ApplicationErrorKind;
  errorMessage?: string;
  diagnostics?: ApiDiagnostics;
  organizations: OrganizationSummary[];
  models: ModelSummary[];
  modelOverview?: ModelOverview;
  selectedOrganizationId: string;
  selectedModelId: string;
  selectOrganization: (organizationId: string) => void;
  selectModel: (modelId: string) => void;
  retry: () => void;
  applicationUrl?: (path: string) => string;
  loadModelGraph: (
    modelId: string,
    options?: GraphNavigationOptions,
  ) => Promise<HeliosGraphDto>;
  loadGraphElementDetail: (
    modelId: string,
    elementId: string,
    reviewRunId?: string,
  ) => Promise<GraphElementDetail>;
  decideModelProposal: (
    modelId: string,
    runId: string,
    decision: ReviewDecisionRequest,
  ) => Promise<ReviewDecisionResponse>;
  loadModelReview: (
    modelId: string,
    runId: string,
  ) => Promise<ReviewSummary>;
  decideModelDataset: (
    modelId: string,
    runId: string,
    table: string,
    decision: "accept" | "reject",
  ) => Promise<ReviewMutationResponse>;
  bulkAcceptModelProposals: (
    modelId: string,
    runId: string,
    minConfidence: number,
  ) => Promise<ReviewMutationResponse>;
  resetModelReview: (
    modelId: string,
    runId: string,
    section?: ReviewSection,
  ) => Promise<ReviewMutationResponse>;
  publishModelReview: (
    modelId: string,
    runId: string,
  ) => Promise<ReviewPublicationResponse>;
  refreshModelOverview: () => void;
  loadModelStatus: (
    modelId: string,
    includeDetails?: boolean,
  ) => Promise<ModelSystemStatus>;
}

function describeError(error: unknown): {
  kind: ApplicationErrorKind;
  message: string;
} {
  if (error instanceof AuthenticationError) {
    return { kind: "authentication", message: error.message };
  }
  if (error instanceof AuthorizationError) {
    return { kind: "authorization", message: error.message };
  }

  return {
    kind: "unavailable",
    message:
      error instanceof ApiUnavailableError
        ? error.message
        : "The Helios API could not be reached.",
  };
}

export function useApplicationContext(
  suppliedClient?: HeliosApi,
): ApplicationContextState {
  const clientRef = useRef<HeliosApi | undefined>(suppliedClient);
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const locationRef = useRef(location);
  locationRef.current = location;
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [modelStatus, setModelStatus] = useState<LoadStatus>("idle");
  const [overviewStatus, setOverviewStatus] = useState<LoadStatus>("idle");
  const [errorKind, setErrorKind] = useState<ApplicationErrorKind>();
  const [errorMessage, setErrorMessage] = useState<string>();
  const [diagnostics, setDiagnostics] = useState<ApiDiagnostics>();
  const [organizations, setOrganizations] = useState<OrganizationSummary[]>([]);
  const [loadedModels, setLoadedModels] = useState<ModelSummary[]>([]);
  const [modelOverview, setModelOverview] = useState<ModelOverview>();
  const [modelsOrganizationId, setModelsOrganizationId] = useState("");
  const [loadVersion, setLoadVersion] = useState(0);
  const [overviewVersion, setOverviewVersion] = useState(0);

  const requestedOrganizationId = searchParams.get("organization") ?? "";
  const requestedModelId = searchParams.get("model") ?? "";
  const selectedOrganizationId =
    organizations.find(
      (organization) => organization.id === requestedOrganizationId,
    )?.id ??
    organizations[0]?.id ??
    "";
  const models =
    modelsOrganizationId === selectedOrganizationId ? loadedModels : [];
  const selectedModelId =
    models.find((model) => model.id === requestedModelId)?.id ??
    models[0]?.id ??
    "";
  const effectiveModelStatus: LoadStatus =
    status === "ready" &&
    selectedOrganizationId &&
    modelsOrganizationId !== selectedOrganizationId
      ? "loading"
      : modelStatus;

  const client = useCallback(() => {
    clientRef.current ??= new HeliosApiClient();
    return clientRef.current;
  }, []);

  const writeContext = useCallback(
    (organizationId: string, modelId: string, replace: boolean) => {
      const current = locationRef.current;
      const next = new URLSearchParams(current.search);
      if (organizationId) {
        next.set("organization", organizationId);
      } else {
        next.delete("organization");
      }
      if (organizationId && modelId) {
        next.set("model", modelId);
      } else {
        next.delete("model");
      }
      const query = next.toString();
      navigate(
        {
          pathname: current.pathname,
          search: query ? `?${query}` : "",
          hash: current.hash,
        },
        { replace },
      );
    },
    [navigate],
  );

  useEffect(() => {
    let active = true;
    setStatus("loading");
    setErrorKind(undefined);
    setErrorMessage(undefined);

    async function load() {
      try {
        const api = client();
        await api.health();
        const [diagnosticResult, organizationResult] = await Promise.all([
          api.diagnostics(),
          api.organizations(),
        ]);
        if (!active) return;
        setDiagnostics(diagnosticResult);
        setOrganizations(organizationResult.organizations);
        setStatus("ready");
      } catch (error) {
        if (!active) return;
        const described = describeError(error);
        setDiagnostics(undefined);
        setOrganizations([]);
        setLoadedModels([]);
        setModelOverview(undefined);
        setErrorKind(described.kind);
        setErrorMessage(described.message);
        setStatus("error");
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [client, loadVersion]);

  useEffect(() => {
    if (status !== "ready") return;
    if (
      requestedOrganizationId &&
      requestedOrganizationId !== selectedOrganizationId
    ) {
      writeContext(selectedOrganizationId, "", true);
    } else if (!requestedOrganizationId && requestedModelId) {
      writeContext("", "", true);
    }
  }, [
    requestedModelId,
    requestedOrganizationId,
    selectedOrganizationId,
    status,
    writeContext,
  ]);

  useEffect(() => {
    let active = true;
    setLoadedModels([]);
    setModelsOrganizationId(selectedOrganizationId);
    if (!selectedOrganizationId || status !== "ready") {
      setModelStatus("idle");
      return () => {
        active = false;
      };
    }

    setModelStatus("loading");
    async function loadModels() {
      try {
        const result = await client().models(selectedOrganizationId);
        if (!active) return;
        setLoadedModels(
          result.models.filter(
            (model) => model.organization_id === selectedOrganizationId,
          ),
        );
        setModelStatus("ready");
      } catch (error) {
        if (!active) return;
        const described = describeError(error);
        setLoadedModels([]);
        setErrorKind(described.kind);
        setErrorMessage(described.message);
        if (
          described.kind === "authentication" ||
          described.kind === "authorization"
        ) {
          setOrganizations([]);
          setStatus("error");
        } else {
          setModelStatus("error");
        }
      }
    }

    void loadModels();
    return () => {
      active = false;
    };
  }, [client, selectedOrganizationId, status]);

  useEffect(() => {
    let active = true;
    setModelOverview((current) =>
      current?.id === selectedModelId ? current : undefined,
    );
    if (
      status !== "ready" ||
      effectiveModelStatus !== "ready" ||
      !selectedModelId
    ) {
      setOverviewStatus("idle");
      return () => {
        active = false;
      };
    }

    setOverviewStatus("loading");
    async function loadOverview() {
      try {
        const result = await client().modelOverview(selectedModelId);
        if (!active) return;
        setModelOverview(result);
        setOverviewStatus("ready");
      } catch (error) {
        if (!active) return;
        const described = describeError(error);
        setModelOverview(undefined);
        setErrorKind(described.kind);
        setErrorMessage(described.message);
        if (
          described.kind === "authentication" ||
          described.kind === "authorization"
        ) {
          setOrganizations([]);
          setLoadedModels([]);
          setStatus("error");
        } else {
          setOverviewStatus("error");
        }
      }
    }

    void loadOverview();
    return () => {
      active = false;
    };
  }, [
    client,
    effectiveModelStatus,
    selectedModelId,
    status,
    loadVersion,
    overviewVersion,
  ]);

  useEffect(() => {
    if (
      status === "ready" &&
      effectiveModelStatus === "ready" &&
      requestedOrganizationId === selectedOrganizationId &&
      requestedModelId &&
      requestedModelId !== selectedModelId
    ) {
      writeContext(selectedOrganizationId, selectedModelId, true);
    }
  }, [
    effectiveModelStatus,
    requestedModelId,
    requestedOrganizationId,
    selectedModelId,
    selectedOrganizationId,
    status,
    writeContext,
  ]);

  const selectOrganization = useCallback(
    (organizationId: string) => {
      if (
        organizations.some(
          (organization) => organization.id === organizationId,
        )
      ) {
        writeContext(organizationId, "", false);
      }
    },
    [organizations, writeContext],
  );

  const selectModel = useCallback(
    (modelId: string) => {
      if (models.some((model) => model.id === modelId)) {
        writeContext(selectedOrganizationId, modelId, false);
      }
    },
    [models, selectedOrganizationId, writeContext],
  );

  const loadModelGraph = useCallback(
    (modelId: string, options?: GraphNavigationOptions) =>
      client().modelGraph(modelId, options),
    [client],
  );
  const loadGraphElementDetail = useCallback(
    (modelId: string, elementId: string, reviewRunId?: string) =>
      client().modelGraphDetail(modelId, elementId, reviewRunId),
    [client],
  );
  const decideModelProposal = useCallback(
    (
      modelId: string,
      runId: string,
      decision: ReviewDecisionRequest,
    ) => client().decideModelProposal(modelId, runId, decision),
    [client],
  );
  const loadModelReview = useCallback(
    (modelId: string, runId: string) =>
      client().modelReview(modelId, runId),
    [client],
  );
  const decideModelDataset = useCallback(
    (
      modelId: string,
      runId: string,
      table: string,
      decision: "accept" | "reject",
    ) => client().decideModelDataset(modelId, runId, table, decision),
    [client],
  );
  const bulkAcceptModelProposals = useCallback(
    (modelId: string, runId: string, minConfidence: number) =>
      client().bulkAcceptModelProposals(
        modelId,
        runId,
        minConfidence,
      ),
    [client],
  );
  const resetModelReview = useCallback(
    (modelId: string, runId: string, section?: ReviewSection) =>
      client().resetModelReview(modelId, runId, section),
    [client],
  );
  const publishModelReview = useCallback(
    (modelId: string, runId: string) =>
      client().publishModelReview(modelId, runId),
    [client],
  );
  const loadModelStatus = useCallback(
    (modelId: string, includeDetails = false) =>
      client().modelStatus(modelId, includeDetails),
    [client],
  );
  const applicationUrl = client().applicationUrl;

  return {
    status,
    modelStatus: effectiveModelStatus,
    overviewStatus,
    errorKind,
    errorMessage,
    diagnostics,
    organizations,
    models,
    modelOverview,
    selectedOrganizationId,
    selectedModelId,
    selectOrganization,
    selectModel,
    retry: () => setLoadVersion((version) => version + 1),
    applicationUrl: applicationUrl
      ? (path: string) => applicationUrl.call(client(), path)
      : undefined,
    loadModelGraph,
    loadGraphElementDetail,
    decideModelProposal,
    loadModelReview,
    decideModelDataset,
    bulkAcceptModelProposals,
    resetModelReview,
    publishModelReview,
    refreshModelOverview: () =>
      setOverviewVersion((version) => version + 1),
    loadModelStatus,
  };
}
