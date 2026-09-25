import { useCallback, useEffect, useRef, useState } from "react";
import {
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import {
  ApiDiagnostics,
  ApiUnavailableError,
  AuditEvent,
  AuditEventCollection,
  AuditEventOptions,
  AuditSessionCollection,
  AuthenticationError,
  AuthorizationError,
  ClientAuditEvent,
  ConversationCollection,
  ConversationDetail,
  PersistedConversationTurn,
  DiscoveryRun,
  DiscoveryRunsResponse,
  HeliosApi,
  HeliosApiClient,
  HeliosGraphDto,
  HistoricalProfileSummary,
  HistoricalTableProfile,
  GlossaryAssignableAsset,
  GlossaryImportResult,
  GlossaryTermResponse,
  GlossaryTermsOptions,
  GlossaryTermsResponse,
  GlossaryTermWrite,
  ModelGlossary,
  GraphNavigationOptions,
  GraphElementDetail,
  ProposalCollection,
  ProposalCollectionOptions,
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
  loadModelRuns: (modelId: string) => Promise<DiscoveryRunsResponse>;
  loadModelRun: (
    modelId: string,
    runId: string,
  ) => Promise<DiscoveryRun>;
  loadModelRunProfileSummary: (
    modelId: string,
    runId: string,
  ) => Promise<HistoricalProfileSummary>;
  loadModelRunTableProfile: (
    modelId: string,
    runId: string,
    tableId: string,
  ) => Promise<HistoricalTableProfile>;
  loadModelRunProposals: (
    modelId: string,
    runId: string,
    options: ProposalCollectionOptions,
  ) => Promise<ProposalCollection>;
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
  loadModelGlossary: (modelId: string) => Promise<ModelGlossary>;
  createModelGlossary: (
    modelId: string,
    body: { name: string; description: string },
  ) => Promise<ModelGlossary>;
  deleteModelGlossary: (modelId: string) => Promise<{ ok: true }>;
  loadModelGlossaryTerms: (
    modelId: string,
    options?: GlossaryTermsOptions,
  ) => Promise<GlossaryTermsResponse>;
  loadModelGlossaryTerm: (
    modelId: string,
    termId: string,
  ) => Promise<GlossaryTermResponse>;
  createModelGlossaryTerm: (
    modelId: string,
    body: GlossaryTermWrite,
  ) => Promise<GlossaryTermResponse>;
  updateModelGlossaryTerm: (
    modelId: string,
    termId: string,
    body: GlossaryTermWrite,
  ) => Promise<GlossaryTermResponse>;
  deleteModelGlossaryTerm: (
    modelId: string,
    termId: string,
  ) => Promise<{ ok: true }>;
  importModelGlossary: (
    modelId: string,
    file: File,
  ) => Promise<GlossaryImportResult>;
  loadGlossaryAssignableAssets: (
    modelId: string,
    query?: string,
  ) => Promise<{ items: GlossaryAssignableAsset[] }>;
  assignModelGlossaryTerm: (
    modelId: string,
    termId: string,
    canvasElementId: string,
  ) => Promise<{ ok: true }>;
  unassignModelGlossaryTerm: (
    modelId: string,
    termId: string,
    assignmentId: string,
  ) => Promise<{ ok: true }>;
  loadModelConversations: (
    modelId: string,
  ) => Promise<ConversationCollection>;
  createModelConversation: (
    modelId: string,
    message: string,
  ) => Promise<PersistedConversationTurn>;
  loadModelConversation: (
    modelId: string,
    conversationId: string,
  ) => Promise<ConversationDetail>;
  appendModelConversationTurn: (
    modelId: string,
    conversationId: string,
    message: string,
    expectedVersion: number,
  ) => Promise<PersistedConversationTurn>;
  loadAuditEvents: (
    options?: AuditEventOptions,
  ) => Promise<AuditEventCollection>;
  loadAuditEvent: (eventId: string) => Promise<AuditEvent>;
  loadAuditSessions: (
    organizationId?: string,
    includeAll?: boolean,
  ) => Promise<AuditSessionCollection>;
  recordClientActivity: (
    event: ClientAuditEvent,
  ) => Promise<{ ok: true }>;
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
        const api = client();
        void api.recordClientAuditEvent?.({
          action: "context.organization_select",
          resource_type: "organization",
          resource_id: organizationId,
        }).catch(() => undefined);
      }
    },
    [client, organizations, writeContext],
  );

  const selectModel = useCallback(
    (modelId: string) => {
      if (models.some((model) => model.id === modelId)) {
        writeContext(selectedOrganizationId, modelId, false);
        const api = client();
        void api.recordClientAuditEvent?.({
          action: "context.model_select",
          resource_type: "model",
          resource_id: modelId,
          model_id: modelId,
        }).catch(() => undefined);
      }
    },
    [client, models, selectedOrganizationId, writeContext],
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
  const loadModelRuns = useCallback(
    (modelId: string) => {
      const api = client();
      if (!api.modelRuns) {
        throw new ApiUnavailableError("Run history is unavailable.");
      }
      return api.modelRuns(modelId);
    },
    [client],
  );
  const loadModelRun = useCallback(
    (modelId: string, runId: string) => {
      const api = client();
      if (!api.modelRun) {
        throw new ApiUnavailableError("Run details are unavailable.");
      }
      return api.modelRun(modelId, runId);
    },
    [client],
  );
  const loadModelRunTableProfile = useCallback(
    (modelId: string, runId: string, tableId: string) => {
      const api = client();
      if (!api.modelRunTableProfile) {
        throw new ApiUnavailableError(
          "Historical table profiles are unavailable.",
        );
      }
      return api.modelRunTableProfile(modelId, runId, tableId);
    },
    [client],
  );
  const loadModelRunProfileSummary = useCallback(
    (modelId: string, runId: string) => {
      const api = client();
      if (!api.modelRunProfileSummary) {
        throw new ApiUnavailableError(
          "Historical profile summaries are unavailable.",
        );
      }
      return api.modelRunProfileSummary(modelId, runId);
    },
    [client],
  );
  const loadModelRunProposals = useCallback(
    (
      modelId: string,
      runId: string,
      options: ProposalCollectionOptions,
    ) => {
      const api = client();
      if (!api.modelRunProposals) {
        throw new ApiUnavailableError("Proposal review is unavailable.");
      }
      return api.modelRunProposals(modelId, runId, options);
    },
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
  const loadModelGlossary = useCallback((modelId: string) => {
    const api = client();
    if (!api.modelGlossary) {
      throw new ApiUnavailableError("Glossary management is unavailable.");
    }
    return api.modelGlossary(modelId);
  }, [client]);
  const createModelGlossary = useCallback((
    modelId: string,
    body: { name: string; description: string },
  ) => {
    const api = client();
    if (!api.createModelGlossary) {
      throw new ApiUnavailableError("Glossary management is unavailable.");
    }
    return api.createModelGlossary(modelId, body);
  }, [client]);
  const deleteModelGlossary = useCallback((modelId: string) => {
    const api = client();
    if (!api.deleteModelGlossary) {
      throw new ApiUnavailableError("Glossary management is unavailable.");
    }
    return api.deleteModelGlossary(modelId);
  }, [client]);
  const loadModelGlossaryTerms = useCallback((
    modelId: string,
    options?: GlossaryTermsOptions,
  ) => {
    const api = client();
    if (!api.modelGlossaryTerms) {
      throw new ApiUnavailableError("Glossary terms are unavailable.");
    }
    return api.modelGlossaryTerms(modelId, options);
  }, [client]);
  const loadModelGlossaryTerm = useCallback((
    modelId: string,
    termId: string,
  ) => {
    const api = client();
    if (!api.modelGlossaryTerm) {
      throw new ApiUnavailableError("Glossary term details are unavailable.");
    }
    return api.modelGlossaryTerm(modelId, termId);
  }, [client]);
  const createModelGlossaryTerm = useCallback((
    modelId: string,
    body: GlossaryTermWrite,
  ) => {
    const api = client();
    if (!api.createModelGlossaryTerm) {
      throw new ApiUnavailableError("Glossary term creation is unavailable.");
    }
    return api.createModelGlossaryTerm(modelId, body);
  }, [client]);
  const updateModelGlossaryTerm = useCallback((
    modelId: string,
    termId: string,
    body: GlossaryTermWrite,
  ) => {
    const api = client();
    if (!api.updateModelGlossaryTerm) {
      throw new ApiUnavailableError("Glossary term editing is unavailable.");
    }
    return api.updateModelGlossaryTerm(modelId, termId, body);
  }, [client]);
  const deleteModelGlossaryTerm = useCallback((
    modelId: string,
    termId: string,
  ) => {
    const api = client();
    if (!api.deleteModelGlossaryTerm) {
      throw new ApiUnavailableError("Glossary term deletion is unavailable.");
    }
    return api.deleteModelGlossaryTerm(modelId, termId);
  }, [client]);
  const importModelGlossary = useCallback((modelId: string, file: File) => {
    const api = client();
    if (!api.importModelGlossary) {
      throw new ApiUnavailableError("Glossary import is unavailable.");
    }
    return api.importModelGlossary(modelId, file);
  }, [client]);
  const loadGlossaryAssignableAssets = useCallback((
    modelId: string,
    query?: string,
  ) => {
    const api = client();
    if (!api.modelGlossaryAssignableAssets) {
      throw new ApiUnavailableError("Glossary assignments are unavailable.");
    }
    return api.modelGlossaryAssignableAssets(modelId, query);
  }, [client]);
  const assignModelGlossaryTerm = useCallback((
    modelId: string,
    termId: string,
    canvasElementId: string,
  ) => {
    const api = client();
    if (!api.assignModelGlossaryTerm) {
      throw new ApiUnavailableError("Glossary assignments are unavailable.");
    }
    return api.assignModelGlossaryTerm(modelId, termId, canvasElementId);
  }, [client]);
  const unassignModelGlossaryTerm = useCallback((
    modelId: string,
    termId: string,
    assignmentId: string,
  ) => {
    const api = client();
    if (!api.unassignModelGlossaryTerm) {
      throw new ApiUnavailableError("Glossary assignments are unavailable.");
    }
    return api.unassignModelGlossaryTerm(modelId, termId, assignmentId);
  }, [client]);
  const loadModelConversations = useCallback((modelId: string) => {
    const api = client();
    if (!api.modelConversations) {
      throw new ApiUnavailableError("Conversations are unavailable.");
    }
    return api.modelConversations(modelId);
  }, [client]);
  const createModelConversation = useCallback((
    modelId: string,
    message: string,
  ) => {
    const api = client();
    if (!api.createModelConversation) {
      throw new ApiUnavailableError("Conversations are unavailable.");
    }
    return api.createModelConversation(modelId, message);
  }, [client]);
  const loadModelConversation = useCallback((
    modelId: string,
    conversationId: string,
  ) => {
    const api = client();
    if (!api.modelConversation) {
      throw new ApiUnavailableError("Conversation details are unavailable.");
    }
    return api.modelConversation(modelId, conversationId);
  }, [client]);
  const appendModelConversationTurn = useCallback((
    modelId: string,
    conversationId: string,
    message: string,
    expectedVersion: number,
  ) => {
    const api = client();
    if (!api.appendModelConversationTurn) {
      throw new ApiUnavailableError("Conversations are unavailable.");
    }
    return api.appendModelConversationTurn(
      modelId,
      conversationId,
      message,
      expectedVersion,
    );
  }, [client]);
  const loadAuditEvents = useCallback((options?: AuditEventOptions) => {
    const api = client();
    if (!api.auditEvents) {
      throw new ApiUnavailableError("Activity logs are unavailable.");
    }
    return api.auditEvents(options);
  }, [client]);
  const loadAuditEvent = useCallback((eventId: string) => {
    const api = client();
    if (!api.auditEvent) {
      throw new ApiUnavailableError("Activity log details are unavailable.");
    }
    return api.auditEvent(eventId);
  }, [client]);
  const loadAuditSessions = useCallback((
    organizationId?: string,
    includeAll = false,
  ) => {
    const api = client();
    if (!api.auditSessions) {
      throw new ApiUnavailableError("Activity log sessions are unavailable.");
    }
    return api.auditSessions(organizationId, includeAll);
  }, [client]);
  const recordClientActivity = useCallback((event: ClientAuditEvent) => {
    const api = client();
    if (!api.recordClientAuditEvent) {
      return Promise.resolve({ ok: true as const });
    }
    return api.recordClientAuditEvent(event);
  }, [client]);
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
    loadModelRuns,
    loadModelRun,
    loadModelRunProfileSummary,
    loadModelRunTableProfile,
    loadModelRunProposals,
    decideModelProposal,
    loadModelReview,
    decideModelDataset,
    bulkAcceptModelProposals,
    resetModelReview,
    publishModelReview,
    refreshModelOverview: () =>
      setOverviewVersion((version) => version + 1),
    loadModelStatus,
    loadModelGlossary,
    createModelGlossary,
    deleteModelGlossary,
    loadModelGlossaryTerms,
    loadModelGlossaryTerm,
    createModelGlossaryTerm,
    updateModelGlossaryTerm,
    deleteModelGlossaryTerm,
    importModelGlossary,
    loadGlossaryAssignableAssets,
    assignModelGlossaryTerm,
    unassignModelGlossaryTerm,
    loadModelConversations,
    createModelConversation,
    loadModelConversation,
    appendModelConversationTurn,
    loadAuditEvents,
    loadAuditEvent,
    loadAuditSessions,
    recordClientActivity,
  };
}
