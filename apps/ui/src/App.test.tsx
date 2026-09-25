import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  ApiDiagnostics,
  AuthenticationError,
  AuthorizationError,
  DiscoveryRun,
  HeliosApi,
  HeliosGraphDto,
  ModelOverview,
  ModelSystemStatus,
  ModelsResponse,
  OrganizationsResponse,
  ProposalCollection,
  ProposalItem,
  ReviewSummary,
} from "./api/client";

const diagnostics: ApiDiagnostics = {
  status: "ok",
  principal: {
    id: "cloudera-workbench:analyst",
    issuer: "cloudera-workbench",
    subject: "analyst",
    display_name: "Data Analyst",
    kind: "human",
  },
  accessible_organization_count: 2,
};

const organizations: OrganizationsResponse = {
  count: 2,
  organizations: [
    { id: "north", name: "North Region", available_actions: [] },
    { id: "south", name: "South Region", available_actions: [] },
  ],
};

const graph: HeliosGraphDto = {
  model_id: "north-model",
  organization_id: "north",
  nodes: [
    {
      id: "domain:north",
      kind: "domain",
      label: "North Model",
      status: "published",
      confidence: null,
      evidence: null,
      metadata: {},
      permitted_actions: ["model.read"],
    },
    {
      id: "dataset:orders",
      kind: "dataset",
      label: "Orders",
      status: "published",
      confidence: null,
      evidence: null,
      metadata: { physical_name: "sales.orders" },
      permitted_actions: ["datasource.read"],
    },
    {
      id: "attribute:orders:id",
      kind: "attribute",
      label: "Order ID",
      status: "published",
      confidence: null,
      evidence: null,
      metadata: { datatype: "string" },
      permitted_actions: ["datasource.read"],
    },
  ],
  edges: [
    {
      id: "contains:orders:id",
      kind: "physical_relationship",
      source: "dataset:orders",
      target: "attribute:orders:id",
      status: "published",
      confidence: null,
      evidence: null,
      metadata: {},
      permitted_actions: ["datasource.read"],
    },
  ],
  summary: {
    node_count: 3,
    edge_count: 1,
    node_kinds: ["attribute", "dataset", "domain"],
    edge_kinds: ["physical_relationship"],
  },
};

function modelsFor(organizationId: string): ModelsResponse {
  return {
    organization_id: organizationId,
    count: 1,
    models: [
      {
        id: `${organizationId}-model`,
        organization_id: organizationId,
        name: `${organizationId === "north" ? "North" : "South"} Model`,
        description: "A governed semantic model.",
        data_sources: [],
        available_actions: ["model.read"],
      },
    ],
  };
}

function overviewFor(modelId: string): ModelOverview {
  const organizationId = modelId.replace(/-model$/, "");
  return {
    ...modelsFor(organizationId).models[0],
    status: "published",
    creator: {
      id: "cloudera-workbench:owner",
      display_name: "Model Owner",
    },
    created_at: "2026-09-22T00:00:00+00:00",
    updated_at: "2026-09-23T00:00:00+00:00",
    data_sources: [
      {
        data_source_id: "warehouse",
        name: "Production Warehouse",
        connector: "impala",
        selected_assets: ["sales.orders"],
      },
    ],
    summary: {
      dataset_count: 12,
      relationship_count: 8,
      concept_count: 3,
      metric_count: 6,
    },
    lifecycle: {
      publication_state: "published",
      discovery_status: "proposals_ready",
      review_status: "pending",
      unresolved_review_items: 2,
      latest_run_id: "run-1",
    },
    available_actions: [
      "model.read",
      "model.edit",
      "discovery.run",
    ],
  };
}

function reviewSummary(
  overrides: Partial<ReviewSummary> = {},
): ReviewSummary {
  const counts = {
    accept: 0,
    reject: 0,
    edit: 0,
    pending: 0,
    total: 0,
  };
  return {
    model_id: "north-model",
    run_id: "run-1",
    sections: {
      datasets: { ...counts, pending: 1, total: 1 },
      fields: { ...counts },
      relationships: { ...counts },
      metrics: { ...counts },
      glossary_terms: { ...counts },
    },
    reviewed_at: null,
    reviewed_by: null,
    preflight_issues: {},
    validation_errors: [],
    publish_ready: true,
    publication: null,
    available_actions: ["decide", "cascade", "bulk_accept", "reset"],
    ...overrides,
  };
}

function systemStatus(
  overrides: Partial<ModelSystemStatus> = {},
): ModelSystemStatus {
  return {
    model_id: "north-model",
    status: "degraded",
    checked_at: "2026-09-23T20:00:00+00:00",
    components: [
      {
        id: "api",
        label: "Helios API",
        status: "healthy",
        description: "The authorized status API responded.",
      },
      {
        id: "semantic-model",
        label: "Semantic model",
        status: "healthy",
        description: "A published semantic model is available.",
      },
      {
        id: "discovery-profile",
        label: "Discovery and profiling",
        status: "degraded",
        description: "Profiling completed; proposals are pending.",
      },
    ],
    details_available: false,
    details: [],
    recent_activity: {
      discovery: {
        run_id: "run-1",
        completed_at: "2026-09-22T10:00:00+00:00",
      },
      profile: {
        run_id: "run-1",
        completed_at: "2026-09-22T11:00:00+00:00",
      },
    },
    issues: ["Profiling completed; proposals are pending."],
    ...overrides,
  };
}

function discoveryRun(
  overrides: Partial<DiscoveryRun> = {},
): DiscoveryRun {
  return {
    id: "run-1",
    type: "discovery",
    model_id: "north-model",
    status: "completed",
    progress: 100,
    initiator: null,
    started_at: "2026-09-22T10:00:00+00:00",
    completed_at: "2026-09-22T10:02:00+00:00",
    duration_seconds: 120,
    warnings: [],
    errors: [],
    stages: { harvest: true, profile: true, propose: true },
    phases: [
      {
        id: "harvest",
        name: "Harvest",
        status: "completed",
        started_at: null,
        completed_at: "2026-09-22T10:00:00+00:00",
        duration_seconds: null,
        counts: { tables: 1, columns: 2 },
        available: true,
      },
      {
        id: "profile",
        name: "Profile",
        status: "completed",
        started_at: null,
        completed_at: "2026-09-22T10:01:00+00:00",
        duration_seconds: null,
        counts: { tables: 1 },
        available: true,
      },
      {
        id: "propose",
        name: "Propose",
        status: "completed",
        started_at: null,
        completed_at: "2026-09-22T10:02:00+00:00",
        duration_seconds: null,
        counts: { datasets: 1, fields: 1 },
        available: true,
      },
    ],
    counts: {
      discovered: { tables: 1, columns: 2 },
      profiled: { tables: 1 },
      proposed: { datasets: 1, fields: 1 },
    },
    data_source: { engine: "impala", databases: ["sales"] },
    provenance: { llm: { provider: "test", model: "fixture" } },
    available_actions: ["model.read", "model.edit", "datasource.read"],
    ...overrides,
  };
}

function proposalItem(
  overrides: Partial<ProposalItem> = {},
): ProposalItem {
  return {
    id: "sales.orders",
    section: "datasets",
    proposal: {
      table: "sales.orders",
      name: "Orders",
      kind: "fact",
      description: "Customer orders.",
      confidence: 0.92,
    },
    confidence: 0.92,
    provenance: {
      source: "profile",
      llm: { provider: "test", model: "fixture" },
    },
    review: { decision: "pending", overrides: null, note: null },
    canvas: {
      review_run_id: "run-1",
      element_id: "dataset:sales.orders",
      focus_node_id: "dataset:sales.orders",
      lens: "semantic",
    },
    available_actions: ["accept", "reject", "edit", "view_in_canvas"],
    ...overrides,
  } as ProposalItem;
}

function proposalCollection(
  item: ProposalItem = proposalItem(),
): ProposalCollection {
  return {
    model_id: "north-model",
    run_id: "run-1",
    section: item.section,
    items: [item],
    page: {
      offset: 0,
      limit: 25,
      returned: 1,
      total: 1,
      has_more: false,
    },
    filters: { decision: null, query: null },
    reviewed_at: null,
    reviewed_by: null,
    available_actions: ["decide", "cascade", "bulk_accept", "reset"],
  };
}

function successfulClient(): HeliosApi {
  return {
    health: vi.fn().mockResolvedValue({ status: "ok" }),
    diagnostics: vi.fn().mockResolvedValue(diagnostics),
    organizations: vi.fn().mockResolvedValue(organizations),
    models: vi.fn((organizationId: string) =>
      Promise.resolve(modelsFor(organizationId)),
    ),
    modelOverview: vi.fn((modelId: string) =>
      Promise.resolve(overviewFor(modelId)),
    ),
    modelRuns: vi.fn().mockResolvedValue({
      model_id: "north-model",
      runs: [discoveryRun()],
      available_actions: ["model.read"],
    }),
    modelRun: vi.fn().mockResolvedValue(discoveryRun()),
    modelRunProfileSummary: vi.fn().mockResolvedValue({
      model_id: "north-model",
      run_id: "run-1",
      profiled_at: "2026-09-22T10:01:00+00:00",
      engine: "impala",
      tables: [
        {
          table_id: "sales.orders",
          harvested: true,
          profiled: true,
          column_count: 2,
          row_count: 1000,
          primary_key_candidates: [
            { column: "order_id", confidence: 0.95 },
          ],
        },
      ],
      relationships: [
        {
          from: "sales.orders",
          from_column: "customer_id",
          to: "sales.customers",
          to_column: "customer_id",
          match_ratio: 1,
          distinct_values: 500,
        },
      ],
      suggested_relationships: [
        {
          from: "sales.orders",
          from_column: "region_id",
          to: "sales.regions",
          to_column: "region_id",
          match_ratio: 0.98,
          distinct_values: 12,
        },
      ],
      rejected_candidates: [
        {
          from: "sales.orders",
          from_column: "legacy_id",
          to: "sales.legacy",
          to_column: "legacy_id",
          match_ratio: 0.1,
          unmatched: 90,
        },
      ],
      provenance: { artifacts: ["harvest", "profile"], run_id: "run-1" },
      available_actions: ["view_table_profile"],
    }),
    modelRunTableProfile: vi.fn().mockResolvedValue({
      model_id: "north-model",
      run_id: "run-1",
      table_id: "sales.orders",
      profiled_at: "2026-09-22T10:01:00+00:00",
      engine: "impala",
      row_count: 1000,
      column_count: 1,
      columns: {
        order_id: {
          type: "bigint",
          null_rate: 0,
          ndv_exact: 1000,
          glossary_terms: ["Order identifier"],
        },
        customer_id: {
          type: "bigint",
          null_rate: 0,
          ndv: 500,
          glossary_terms: [],
        },
      },
      primary_key_candidates: [{ column: "order_id", confidence: 0.95 }],
      relationships: [
        {
          from: "sales.orders",
          from_column: "customer_id",
          to: "sales.customers",
          to_column: "customer_id",
          match_ratio: 1,
          distinct_values: 500,
        },
      ],
      provenance: {
        artifact: "profile",
        run_id: "run-1",
        table_id: "sales.orders",
      },
      canvas: {
        element_id: "dataset:sales.orders",
        focus_node_id: "dataset:sales.orders",
        lens: "physical",
      },
      available_actions: ["view_in_canvas"],
    }),
    modelRunProposals: vi.fn().mockResolvedValue(proposalCollection()),
    modelStatus: vi.fn().mockResolvedValue(systemStatus()),
    modelGraph: vi.fn().mockResolvedValue(graph),
    modelGraphDetail: vi.fn().mockResolvedValue({
      element_type: "node",
      id: "dataset:orders",
      kind: "dataset",
      label: "Orders",
      source: null,
      target: null,
      status: "published",
      confidence: null,
      evidence: null,
      details: {
        physical_identity: "sales.orders",
        row_count: 1000,
      },
      available_actions: ["datasource.read"],
    }),
    decideModelProposal: vi.fn().mockResolvedValue({
      ok: true,
      run_id: "run-1",
      section: "datasets",
      element_id: "sales.orders",
      entry: { decision: "accept" },
      reviewed_at: "2026-09-23T00:00:00+00:00",
      reviewed_by: "cloudera-workbench:analyst",
      summary: reviewSummary(),
    }),
    modelReview: vi.fn().mockResolvedValue(reviewSummary()),
    decideModelDataset: vi.fn().mockResolvedValue({
      ok: true,
      changed: 2,
      summary: reviewSummary(),
    }),
    bulkAcceptModelProposals: vi.fn().mockResolvedValue({
      ok: true,
      changed: 1,
      summary: reviewSummary(),
    }),
    resetModelReview: vi.fn().mockResolvedValue({
      ok: true,
      summary: reviewSummary(),
    }),
    publishModelReview: vi.fn().mockResolvedValue({
      ok: true,
      model_id: "north-model",
      run_id: "run-1",
      manifest: {},
    }),
    modelGlossary: vi.fn().mockResolvedValue({
      model_id: "north-model",
      glossary: {
        id: "north-glossary",
        name: "North business glossary",
        description: "Governed terms for the selected model.",
        term_count: 1,
      },
      available_actions: ["glossary.read", "glossary.edit"],
    }),
    modelGlossaryTerms: vi.fn().mockResolvedValue({
      model_id: "north-model",
      glossary_id: "north-glossary",
      items: [
        {
          id: "order-id",
          name: "Order identifier",
          definition: "A stable identifier for an order.",
          long_description: "",
          abbreviation: "OID",
          examples: [],
          status: "published",
          confidence: null,
          evidence: [],
        },
      ],
      offset: 0,
      limit: 24,
      total: 1,
      truncated: false,
      available_actions: ["glossary.read", "glossary.edit"],
    }),
    modelGlossaryTerm: vi.fn().mockResolvedValue({
      term: {
        id: "order-id",
        name: "Order identifier",
        definition: "A stable identifier for an order.",
        long_description: "Used across governed order datasets.",
        abbreviation: "OID",
        examples: ["O-100"],
        status: "published",
        confidence: null,
        evidence: [],
        assignments: [
          {
            id: "atlas-column-1",
            name: "sales.orders.order_id",
            type: "hive_column",
            canvas_element_id: "attribute:orders:id",
            canvas_lens: "physical",
          },
        ],
      },
      available_actions: ["glossary.read", "glossary.edit"],
    }),
    createModelGlossary: vi.fn(),
    deleteModelGlossary: vi.fn().mockResolvedValue({ ok: true }),
    createModelGlossaryTerm: vi.fn().mockResolvedValue({
      term: {
        id: "new-term",
        name: "New term",
        definition: "",
        long_description: "",
        abbreviation: "",
        examples: [],
        status: "published",
        confidence: null,
        evidence: [],
      },
      available_actions: ["glossary.read", "glossary.edit"],
    }),
    updateModelGlossaryTerm: vi.fn(),
    deleteModelGlossaryTerm: vi.fn().mockResolvedValue({ ok: true }),
    importModelGlossary: vi.fn().mockResolvedValue({
      ok: true,
      imported: 2,
      failed: 0,
    }),
    modelGlossaryAssignableAssets: vi.fn().mockResolvedValue({
      items: [],
    }),
    assignModelGlossaryTerm: vi.fn().mockResolvedValue({ ok: true }),
    unassignModelGlossaryTerm: vi.fn().mockResolvedValue({ ok: true }),
    modelConversations: vi.fn().mockResolvedValue({
      model_id: "north-model",
      conversations: [],
    }),
    createModelConversation: vi.fn(),
    modelConversation: vi.fn(),
    appendModelConversationTurn: vi.fn(),
    auditEvents: vi.fn().mockResolvedValue({
      items: [
        {
          id: "audit-1",
          occurred_at: "2026-09-24T12:00:00+00:00",
          request_id: "request-1",
          session_id: "session-1",
          principal_id: "cloudera-workbench:analyst",
          organization_id: "north",
          model_id: "north-model",
          component: "api",
          event_type: "http.request",
          action: "GET /api/v1/models/{model_id}",
          resource_type: "model",
          resource_id: "north-model",
          outcome: "success",
          severity: "info",
          http_status: 200,
          duration_ms: 12.5,
          summary: "API request completed",
          details: { method: "GET" },
        },
      ],
      page: {
        offset: 0,
        limit: 50,
        returned: 1,
        total: 1,
        has_more: false,
      },
      filters: {},
      available_actions: ["audit.read"],
    }),
    auditEvent: vi.fn().mockResolvedValue({
      id: "audit-1",
      occurred_at: "2026-09-24T12:00:00+00:00",
      request_id: "request-1",
      session_id: "session-1",
      principal_id: "cloudera-workbench:analyst",
      organization_id: "north",
      model_id: "north-model",
      component: "api",
      event_type: "http.request",
      action: "GET /api/v1/models/{model_id}",
      resource_type: "model",
      resource_id: "north-model",
      outcome: "success",
      severity: "info",
      http_status: 200,
      duration_ms: 12.5,
      summary: "API request completed",
      details: { method: "GET" },
    }),
    auditSessions: vi.fn().mockResolvedValue({
      sessions: [
        {
          session_id: "session-1",
          principal_id: "cloudera-workbench:analyst",
          first_seen_at: "2026-09-24T11:00:00+00:00",
          last_seen_at: "2026-09-24T12:00:00+00:00",
          event_count: 3,
          organization_id: "north",
        },
      ],
      available_actions: ["audit.read"],
    }),
    recordClientAuditEvent: vi.fn().mockResolvedValue({ ok: true }),
    applicationUrl: vi.fn(
      (path: string) => `https://helios-api.example.test${path}`,
    ),
  };
}

describe("Helios application shell", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders loading controls and status while API data is pending", () => {
    const pending = new Promise<never>(() => undefined);
    const client: HeliosApi = {
      health: vi.fn(() => pending),
      diagnostics: vi.fn(() => pending),
      organizations: vi.fn(() => pending),
      models: vi.fn(() => pending),
      modelOverview: vi.fn(() => pending),
      modelStatus: vi.fn(() => pending),
      modelGraph: vi.fn(() => pending),
      modelGraphDetail: vi.fn(() => pending),
      decideModelProposal: vi.fn(() => pending),
      modelReview: vi.fn(() => pending),
      decideModelDataset: vi.fn(() => pending),
      bulkAcceptModelProposals: vi.fn(() => pending),
      resetModelReview: vi.fn(() => pending),
      publishModelReview: vi.fn(() => pending),
    };

    render(<App client={client} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading Helios");
    expect(screen.getByLabelText("Organization")).toBeDisabled();
    expect(screen.getByLabelText("Model")).toBeDisabled();
  });

  it("loads API-backed selectors, account identity, and model overview", async () => {
    const client = successfulClient();
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "North Model" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Organization")).toHaveValue("north");
    expect(await screen.findByLabelText("Model")).toHaveValue("north-model");
    expect(screen.getByText("Data Analyst")).toBeInTheDocument();
    expect(screen.getByText(/^Build \S+$/)).toBeInTheDocument();
    expect(await screen.findByText("12")).toBeInTheDocument();
    expect(screen.getByText("Production Warehouse")).toBeInTheDocument();
    expect(screen.getByText("2 unresolved items")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Canvas" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review Proposals" }))
      .toHaveAttribute(
        "href",
        "/canvas?organization=north&model=north-model&review_run_id=run-1",
      );
    expect(document.querySelector('a[href^="/runs/"]')).toBeNull();
    expect(screen.queryByRole("link", { name: "Publish" }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary navigation" }))
      .toHaveTextContent("Governance");
    expect(
      await screen.findByRole("heading", { name: "Helios health" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Degraded")).not.toHaveLength(0);
    expect(screen.getByText("Helios API")).toBeInTheDocument();
    expect(screen.queryByText("Metadata repository")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Show system details" }),
    ).not.toBeInTheDocument();
    expect(client.modelStatus).toHaveBeenCalledWith("north-model", false);
  });

  it("loads infrastructure checks only when an administrator expands them", async () => {
    const client = successfulClient();
    vi.mocked(client.modelStatus).mockImplementation(
      (_modelId, includeDetails) =>
        Promise.resolve(
          systemStatus({
            details_available: true,
            details: includeDetails
              ? [
                  {
                    id: "metadata",
                    label: "Metadata repository",
                    status: "healthy",
                    description: "Metadata repository is available.",
                  },
                  {
                    id: "atlas",
                    label: "Atlas",
                    status: "unavailable",
                    description: "Atlas connectivity check failed.",
                  },
                ]
              : [],
          }),
        ),
    );

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Helios health" });
    expect(screen.queryByText("Metadata repository")).not.toBeInTheDocument();

    fireEvent.click(
      await screen.findByRole("button", { name: "Show system details" }),
    );

    expect(await screen.findByText("Metadata repository"))
      .toBeInTheDocument();
    expect(screen.getByText("Atlas connectivity check failed."))
      .toBeInTheDocument();
    expect(client.modelStatus).toHaveBeenLastCalledWith(
      "north-model",
      true,
    );
  });

  it("shows a model status loading state independently", async () => {
    const client = successfulClient();
    vi.mocked(client.modelStatus).mockImplementation(
      () => new Promise<ModelSystemStatus>(() => undefined),
    );

    render(<App client={client} />);

    await screen.findByRole("heading", { name: "North Model" });
    expect(await screen.findByText("Checking model services…"))
      .toBeInTheDocument();
    expect(screen.getByText("Production Warehouse")).toBeInTheDocument();
  });

  it("shows status errors and retries without hiding the model overview", async () => {
    const client = successfulClient();
    vi.mocked(client.modelStatus)
      .mockRejectedValueOnce(new Error("Status request failed."))
      .mockResolvedValueOnce(systemStatus());

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North Model" });
    expect(await screen.findByRole("alert"))
      .toHaveTextContent("Status request failed.");

    fireEvent.click(screen.getByRole("button", { name: "Retry status" }));

    expect(await screen.findByText("Discovery and profiling"))
      .toBeInTheDocument();
    expect(client.modelStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Production Warehouse")).toBeInTheDocument();
  });

  it("loads models from the API when the organization changes", async () => {
    const client = successfulClient();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North Model" });

    fireEvent.change(screen.getByLabelText("Organization"), {
      target: { value: "south" },
    });

    expect(
      await screen.findByRole("heading", { name: "South Model" }),
    ).toBeInTheDocument();
    expect(client.models).toHaveBeenLastCalledWith("south");
  });

  it("collapses the desktop workspace navigation without hiding its links", async () => {
    render(<App client={successfulClient()} />);
    await screen.findByRole("heading", { name: "North Model" });

    const navigation = screen.getByRole("navigation", {
      name: "Primary navigation",
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Collapse workspace" }),
    );

    expect(navigation).toHaveClass("primary-nav--collapsed");
    expect(navigation.parentElement).toHaveClass(
      "app__body--workspace-collapsed",
    );
    expect(screen.getByRole("link", { name: "Canvas" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Expand workspace" }),
    ).toHaveAttribute("aria-expanded", "false");
    await waitFor(() =>
      expect(window.localStorage.getItem("helios.workspace.collapsed"))
        .toBe("true"),
    );
  });

  it("renders authentication errors without fabricating workspace data", async () => {
    const client: HeliosApi = {
      health: vi.fn().mockResolvedValue({ status: "ok" }),
      diagnostics: vi
        .fn()
        .mockRejectedValue(new AuthenticationError("Sign in required.")),
      organizations: vi.fn().mockResolvedValue(organizations),
      models: vi.fn().mockResolvedValue(modelsFor("north")),
      modelOverview: vi.fn().mockResolvedValue(overviewFor("north-model")),
      modelStatus: vi.fn().mockResolvedValue(systemStatus()),
      modelGraph: vi.fn().mockResolvedValue(graph),
      modelGraphDetail: vi.fn().mockResolvedValue({
        element_type: "node",
        id: "dataset:orders",
        kind: "dataset",
        label: "Orders",
        source: null,
        target: null,
        status: "published",
        confidence: null,
        evidence: null,
        details: {},
        available_actions: [],
      }),
      decideModelProposal: vi.fn().mockResolvedValue({
        ok: true,
        run_id: "run-1",
        section: "datasets",
        element_id: "sales.orders",
        entry: { decision: "accept" },
        reviewed_at: "2026-09-23T00:00:00+00:00",
        reviewed_by: "cloudera-workbench:analyst",
      summary: reviewSummary(),
      }),
      modelReview: vi.fn().mockResolvedValue(reviewSummary()),
      decideModelDataset: vi.fn().mockResolvedValue({
        ok: true,
        summary: reviewSummary(),
      }),
      bulkAcceptModelProposals: vi.fn().mockResolvedValue({
        ok: true,
        summary: reviewSummary(),
      }),
      resetModelReview: vi.fn().mockResolvedValue({
        ok: true,
        summary: reviewSummary(),
      }),
      publishModelReview: vi.fn().mockResolvedValue({
        ok: true,
        model_id: "north-model",
        run_id: "run-1",
        manifest: {},
      }),
    };

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Authentication required" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("North Region")).not.toBeInTheDocument();
  });

  it("renders an empty state when the API returns no organizations", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/?organization=removed&model=removed-model",
    );
    vi.mocked(client.organizations).mockResolvedValue({
      organizations: [],
      count: 0,
    });

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "No organizations available" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(client.models).not.toHaveBeenCalled());
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(screen.getByRole("button", { name: "Refresh access" }))
      .toBeInTheDocument();
  });

  it("loads the selected model graph on the canvas route", async () => {
    const client = successfulClient();
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North Model" });

    fireEvent.click(screen.getByRole("link", { name: "Canvas" }));

    expect(
      await screen.findByRole("heading", { name: "North Model" }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/1 attributes are hidden/i))
      .toBeInTheDocument();
    expect(screen.queryByText("Semantic model")).not.toBeInTheDocument();
    expect(screen.queryByText(/Explore the authorized domains/i))
      .not.toBeInTheDocument();
    expect(document.getElementById("main-content"))
      .toHaveClass("app__main--canvas");
    expect(screen.queryByText("Nothing selected")).not.toBeInTheDocument();
    expect(client.modelGraph).toHaveBeenCalledWith("north-model", {
      navigation: true,
      lens: "semantic",
      depth: 1,
      limit: 120,
    });
    fireEvent.change(screen.getByRole("searchbox", { name: "Search model graph" }), {
      target: { value: "Orders" },
    });
    await waitFor(() =>
      expect(client.modelGraph).toHaveBeenCalledWith("north-model", {
        navigation: true,
        lens: "semantic",
        query: "Orders",
        limit: 20,
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Orders dataset" }),
    );
    await waitFor(() =>
      expect(client.modelGraph).toHaveBeenCalledWith("north-model", {
        navigation: true,
        lens: "semantic",
        focusNodeId: "dataset:orders",
        depth: 1,
        includeAttributes: true,
        limit: 120,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Physical" }));
    await waitFor(() =>
      expect(client.modelGraph).toHaveBeenCalledWith("north-model", {
        navigation: true,
        lens: "physical",
        focusNodeId: "dataset:orders",
        depth: 1,
        limit: 120,
      }),
    );
    await waitFor(() =>
      expect(window.location.search).toContain("lens=physical"),
    );
    expect(window.location.search).toContain("organization=north");
    expect(window.location.search).toContain("model=north-model");
  });

  it("reviews a proposal and reconciles it with the API response", async () => {
    const client = successfulClient();
    let approved = false;
    const reviewGraph = () => ({
      ...graph,
      nodes: graph.nodes.map((node) =>
        node.id === "dataset:orders"
          ? {
              ...node,
              status: approved ? "approved" : "needs_review",
              confidence: 0.92,
              metadata: {
                ...node.metadata,
                review_section: "datasets",
                review_element_id: "sales.orders",
              },
              permitted_actions: ["datasource.read", "model.edit"],
            }
          : node,
      ),
    });
    vi.mocked(client.modelGraph).mockImplementation((_modelId, options) =>
      Promise.resolve(options?.reviewRunId ? reviewGraph() : graph),
    );
    vi.mocked(client.modelGraphDetail).mockImplementation(
      (_modelId, _elementId, reviewRunId) =>
        Promise.resolve({
          element_type: "node",
          id: "dataset:orders",
          kind: "dataset",
          label: "Orders",
          source: null,
          target: null,
          status:
            reviewRunId && approved ? "approved" : "needs_review",
          confidence: 0.92,
          evidence: "Catalog metadata and profile evidence",
          details: {
            physical_identity: "sales.orders",
            review_section: "datasets",
            review_element_id: "sales.orders",
            ...(approved
              ? {
                  review_decision: "accept",
                  reviewed_by: "cloudera-workbench:analyst",
                }
              : {}),
          },
          available_actions: ["datasource.read", "model.edit"],
        }),
    );
    vi.mocked(client.decideModelProposal).mockImplementation(
      async (_modelId, runId, request) => {
        approved = true;
        return {
          ok: true,
          run_id: runId,
          section: request.section,
          element_id: request.element_id,
          entry: { decision: request.decision },
          reviewed_at: "2026-09-23T00:00:00+00:00",
          reviewed_by: "cloudera-workbench:analyst",
          summary: reviewSummary({
            reviewed_at: "2026-09-23T00:00:00+00:00",
            reviewed_by: "cloudera-workbench:analyst",
          }),
        };
      },
    );

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North Model" });
    fireEvent.click(
      await screen.findByRole("link", { name: "Review Proposals" }),
    );
    expect(
      await screen.findByRole("button", { name: "Reviewing proposals" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(window.location.search).toContain("review_run_id=run-1");
    await waitFor(() =>
      expect(client.modelGraph).toHaveBeenCalledWith(
        "north-model",
        expect.objectContaining({ reviewRunId: "run-1" }),
      ),
    );

    const reviewStatus = await screen.findByText("Needs Review");
    const reviewNode = reviewStatus.closest(".react-flow__node");
    if (!reviewNode) throw new Error("Review node was not rendered.");
    fireEvent.click(reviewNode);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Approve dataset and attributes",
      }),
    );
    await waitFor(() =>
      expect(client.decideModelDataset).toHaveBeenCalledWith(
        "north-model",
        "run-1",
        "sales.orders",
        "accept",
      ),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve" }),
    );

    await waitFor(() =>
      expect(client.decideModelProposal).toHaveBeenCalledWith(
        "north-model",
        "run-1",
        {
          section: "datasets",
          element_id: "sales.orders",
          decision: "accept",
          overrides: undefined,
          note: "",
        },
      ),
    );
    await waitFor(() =>
      expect(client.modelGraphDetail).toHaveBeenLastCalledWith(
        "north-model",
        "dataset:orders",
        "run-1",
      ),
    );
    expect(await screen.findAllByText("Approved")).not.toHaveLength(0);
    expect(screen.getByText("cloudera-workbench:analyst"))
      .toBeInTheDocument();
  });

  it("runs authorized bulk, reset, and publish review actions", async () => {
    const client = successfulClient();
    const summary = reviewSummary({
      available_actions: [
        "decide",
        "cascade",
        "bulk_accept",
        "reset",
        "publish",
      ],
    });
    vi.mocked(client.modelReview).mockResolvedValue(summary);
    vi.mocked(client.bulkAcceptModelProposals).mockResolvedValue({
      ok: true,
      changed: 1,
      summary,
    });
    vi.mocked(client.resetModelReview).mockResolvedValue({
      ok: true,
      summary,
    });
    vi.mocked(client.decideModelDataset).mockResolvedValue({
      ok: true,
      changed: 2,
      summary,
    });
    vi.mocked(client.modelGraphDetail).mockResolvedValue({
      element_type: "node",
      id: "dataset:orders",
      kind: "dataset",
      label: "Orders",
      source: null,
      target: null,
      status: "needs_review",
      confidence: 0.92,
      evidence: "Profile evidence",
      details: {
        physical_identity: "sales.orders",
        review_section: "datasets",
        review_element_id: "sales.orders",
      },
      available_actions: ["model.edit"],
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North Model" });
    fireEvent.click(
      await screen.findByRole("link", { name: "Review Proposals" }),
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Accept above threshold",
      }),
    );
    await waitFor(() =>
      expect(client.bulkAcceptModelProposals).toHaveBeenCalledWith(
        "north-model",
        "run-1",
        0.85,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Reset review" }));
    await waitFor(() =>
      expect(client.resetModelReview).toHaveBeenCalledWith(
        "north-model",
        "run-1",
        undefined,
      ),
    );
    expect(confirm).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Publish model" }));
    await waitFor(() =>
      expect(client.publishModelReview).toHaveBeenCalledWith(
        "north-model",
        "run-1",
      ),
    );
    expect(
      await screen.findByText("Model published successfully."),
    ).toBeInTheDocument();
    confirm.mockRestore();
  });

  it("keeps review state visible when publishing fails", async () => {
    const client = successfulClient();
    vi.mocked(client.modelReview).mockResolvedValue(
      reviewSummary({
        available_actions: ["publish"],
      }),
    );
    vi.mocked(client.publishModelReview).mockRejectedValue(
      new Error("A relationship references a missing dataset."),
    );

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North Model" });
    fireEvent.click(
      await screen.findByRole("link", { name: "Review Proposals" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Publish model" }),
    );

    expect(
      await screen.findByRole("alert"),
    ).toHaveTextContent("A relationship references a missing dataset.");
    expect(screen.getByText("Review run")).toBeInTheDocument();
    expect(screen.queryByText("Model published successfully."))
      .not.toBeInTheDocument();
  });

  it("restores authorized context from a deep link", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/canvas?organization=south&model=south-model",
    );

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Canvas" }),
    ).toBeInTheDocument();
    expect(await screen.findByLabelText("Organization")).toHaveValue("south");
    expect(await screen.findByLabelText("Model")).toHaveValue("south-model");
    expect(client.models).toHaveBeenCalledWith("south");

    fireEvent.click(screen.getByRole("link", { name: "Overview" }));
    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toContain("organization=south");
    expect(window.location.search).toContain("model=south-model");
  });

  it("replaces inaccessible deep-link context with API-authorized data", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/?organization=hidden&model=secret",
    );

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "North Model" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(window.location.search).toContain("organization=north");
    });
    expect(screen.getByLabelText("Model")).toHaveValue("north-model");
    expect(screen.getByRole("link", { name: "Canvas" }))
      .toHaveAttribute(
        "href",
        "/canvas?organization=north&model=north-model",
      );
    expect(window.location.search).not.toContain("hidden");
    expect(window.location.search).not.toContain("secret");
    expect(client.models).not.toHaveBeenCalledWith("hidden");
  });

  it("handles an authorized organization with no visible models", async () => {
    const client = successfulClient();
    vi.mocked(client.models).mockResolvedValue({
      organization_id: "north",
      models: [],
      count: 0,
    });

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "No models available" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Organization")).toHaveValue("north");
    expect(screen.getByLabelText("Model")).toBeDisabled();
    expect(window.location.search).not.toContain("model=");
  });

  it("renders model-scoped runs with evidence-backed and unavailable lifecycle values", async () => {
    const client = successfulClient();
    vi.mocked(client.modelRuns!).mockResolvedValue({
      model_id: "north-model",
      runs: [
        discoveryRun(),
        discoveryRun({
          id: "run-artifacts-missing",
          status: null,
          progress: null,
          started_at: null,
          completed_at: null,
          duration_seconds: null,
          missing: true,
          phases: [
            {
              id: "harvest",
              name: "Harvest",
              status: null,
              started_at: null,
              completed_at: null,
              duration_seconds: null,
              counts: {},
              available: false,
            },
          ],
          stages: { harvest: false, profile: false, propose: false },
        }),
      ],
      available_actions: ["model.read"],
    });
    window.history.replaceState(
      {},
      "",
      "/models?organization=north&model=north-model",
    );

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Discovery & Activity" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "run-1" })).toHaveAttribute(
      "href",
      "/models/runs/run-1?organization=north&model=north-model",
    );
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Harvest").length).toBeGreaterThan(0);
    expect(screen.queryByText("Validate")).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="/runs/"]')).toBeNull();
    expect(client.modelRuns).toHaveBeenCalledWith("north-model");
  });

  it("renders run detail diagnostics, only returned phases, and preserved context", async () => {
    const client = successfulClient();
    vi.mocked(client.modelRun!).mockResolvedValue(
      discoveryRun({
        status: "completed_with_warnings",
        warnings: ["Profile sampling was limited."],
        errors: [{ code: "row-read", message: "One partition was skipped." }],
        phases: [discoveryRun().phases[0]],
      }),
    );
    window.history.replaceState(
      {},
      "",
      "/models/runs/run-1?organization=north&model=north-model",
    );

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "run-1" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Completed With Warnings")).toBeInTheDocument();
    expect(screen.getByText("Profile sampling was limited.")).toBeInTheDocument();
    expect(screen.getByText(/One partition was skipped/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Harvest" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Profile" }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Discovery & Activity" }))
      .toHaveAttribute(
        "href",
        "/models?organization=north&model=north-model",
      );
    expect(
      await screen.findByRole("link", { name: "sales.orders" }),
    ).toHaveAttribute(
      "href",
      "/models/runs/run-1/profile/sales.orders?organization=north&model=north-model",
    );
    expect(screen.getByText("sales.orders.customer_id")).toBeInTheDocument();
    expect(screen.getByText("sales.orders.region_id")).toBeInTheDocument();
    expect(screen.getByText("sales.orders.legacy_id")).toBeInTheDocument();
    expect(client.modelRunProfileSummary).toHaveBeenCalledWith(
      "north-model",
      "run-1",
    );
  });

  it("renders historical keys, glossary, cardinality accuracy, and relationships", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/models/runs/run-1/profile/sales.orders?organization=north&model=north-model",
    );

    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "sales.orders" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Primary key candidates" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("order_id")).toHaveLength(2);
    expect(screen.getByText(/95% confidence/)).toBeInTheDocument();
    expect(screen.getByText("Order identifier")).toBeInTheDocument();
    expect(screen.getByText("1,000 exact")).toBeInTheDocument();
    expect(screen.getByText("≈ 500")).toBeInTheDocument();
    expect(screen.getByText("sales.orders.customer_id")).toBeInTheDocument();
    expect(screen.getByText("sales.customers.customer_id")).toBeInTheDocument();
  });

  it("shows empty, unavailable, and denied run states without legacy links", async () => {
    const client = successfulClient();
    vi.mocked(client.modelRuns!).mockResolvedValue({
      model_id: "north-model",
      runs: [],
      available_actions: ["model.read"],
    });
    window.history.replaceState(
      {},
      "",
      "/models?organization=north&model=north-model",
    );
    const { unmount } = render(<App client={client} />);
    expect(
      await screen.findByRole("heading", { name: "No discovery activity" }),
    ).toBeInTheDocument();
    unmount();

    vi.mocked(client.modelRuns!).mockRejectedValue(
      new AuthorizationError("Run history access denied."),
    );
    render(<App client={client} />);
    expect(
      await screen.findByRole("heading", {
        name: "Discovery activity could not be loaded",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Run history access denied.")).toBeInTheDocument();
    expect(document.querySelector('a[href^="/runs/"]')).toBeNull();
  });

  it("renders and filters proposal sections and submits typed edit decisions", async () => {
    const client = successfulClient();
    vi.mocked(client.modelRunProposals!).mockImplementation(
      async (_modelId, _runId, options) => {
        const item =
          options.section === "metrics"
            ? proposalItem({
                id: "sales.orders::Revenue",
                section: "metrics",
                proposal: {
                  name: "Revenue",
                  dataset: "sales.orders",
                  expression: "sum(total)",
                  description: "Gross revenue.",
                  confidence: 0.88,
                },
                canvas: {
                  review_run_id: "run-1",
                  element_id: "metric:Revenue",
                  focus_node_id: "metric:Revenue",
                  lens: "semantic",
                },
              })
            : proposalItem();
        return {
          ...proposalCollection(item),
          filters: {
            decision: options.decision ?? null,
            query: options.query ?? null,
          },
        };
      },
    );
    window.history.replaceState(
      {},
      "",
      "/models/runs/run-1?organization=north&model=north-model",
    );

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Proposal workspace" });
    expect(await screen.findByText("Customer orders.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Decision"), {
      target: { value: "pending" },
    });
    fireEvent.change(screen.getByLabelText("Search this section"), {
      target: { value: "orders" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() =>
      expect(client.modelRunProposals).toHaveBeenLastCalledWith(
        "north-model",
        "run-1",
        expect.objectContaining({
          section: "datasets",
          decision: "pending",
          query: "orders",
        }),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Business name"), {
      target: { value: "Customer Orders" },
    });
    fireEvent.change(screen.getByLabelText("Reviewer note"), {
      target: { value: "Use the governed label." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
    await waitFor(() =>
      expect(client.decideModelProposal).toHaveBeenCalledWith(
        "north-model",
        "run-1",
        expect.objectContaining({
          section: "datasets",
          element_id: "sales.orders",
          decision: "edit",
          overrides: expect.objectContaining({
            name: "Customer Orders",
            kind: "fact",
          }),
          note: "Use the governed label.",
        }),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /^Metrics/ }));
    expect(
      await screen.findByRole("heading", { name: "Revenue" }),
    ).toBeInTheDocument();
    expect(screen.getByText("sum(total)")).toBeInTheDocument();
    expect(client.modelRunProposals).toHaveBeenLastCalledWith(
      "north-model",
      "run-1",
      expect.objectContaining({ section: "metrics" }),
    );
  });

  it("supports approve, reject, dataset cascade, bulk, reset, and publish feedback", async () => {
    const client = successfulClient();
    const actionable = reviewSummary({
      available_actions: [
        "decide",
        "cascade",
        "bulk_accept",
        "reset",
        "publish",
      ],
    });
    vi.mocked(client.modelReview).mockResolvedValue(actionable);
    vi.mocked(client.modelRunProposals!).mockResolvedValue(proposalCollection());
    vi.mocked(client.decideModelProposal).mockResolvedValue({
      ok: true,
      run_id: "run-1",
      section: "datasets",
      element_id: "sales.orders",
      entry: { decision: "accept" },
      reviewed_at: "2026-09-23T00:00:00+00:00",
      reviewed_by: "cloudera-workbench:analyst",
      summary: actionable,
    });
    vi.mocked(client.decideModelDataset).mockResolvedValue({
      ok: true,
      changed: 3,
      summary: actionable,
    });
    vi.mocked(client.bulkAcceptModelProposals).mockResolvedValue({
      ok: true,
      changed: 4,
      summary: actionable,
    });
    vi.mocked(client.resetModelReview).mockResolvedValue({
      ok: true,
      summary: actionable,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.history.replaceState(
      {},
      "",
      "/models/runs/run-1?organization=north&model=north-model",
    );

    render(<App client={client} />);
    await screen.findByRole("heading", { name: "Proposal workspace" });
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(client.decideModelProposal).toHaveBeenLastCalledWith(
        "north-model",
        "run-1",
        expect.objectContaining({ decision: "accept" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(client.decideModelProposal).toHaveBeenLastCalledWith(
        "north-model",
        "run-1",
        expect.objectContaining({ decision: "reject" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve all" }));
    expect(
      await screen.findByText("Accepted the dataset and 2 fields."),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Accept above threshold" }),
    );
    expect(
      await screen.findByText("Accepted 4 pending proposals."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset section" }));
    expect(
      await screen.findByText("Reset decisions in Datasets."),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Publish reviewed model" }),
    );
    expect(
      await screen.findByText(
        "The reviewed model was published successfully.",
      ),
    ).toBeInTheDocument();
  });

  it("polls active runs until terminal and does not poll null or terminal runs", async () => {
    vi.useFakeTimers();
    const client = successfulClient();
    vi.mocked(client.modelRuns!)
      .mockResolvedValueOnce({
        model_id: "north-model",
        runs: [discoveryRun({ status: "running", progress: 50 })],
        available_actions: ["model.read"],
      })
      .mockResolvedValueOnce({
        model_id: "north-model",
        runs: [discoveryRun({ status: "completed", progress: 100 })],
        available_actions: ["model.read"],
      });
    window.history.replaceState(
      {},
      "",
      "/models?organization=north&model=north-model",
    );
    render(<App client={client} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(client.modelRuns).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(client.modelRuns).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(client.modelRuns).toHaveBeenCalledTimes(2);
    expect(client.modelOverview).toHaveBeenCalledTimes(2);
  });

  it("does not poll evidence-backed null lifecycle runs", async () => {
    vi.useFakeTimers();
    const client = successfulClient();
    vi.mocked(client.modelRuns!).mockResolvedValue({
      model_id: "north-model",
      runs: [discoveryRun({ status: null, progress: null })],
      available_actions: ["model.read"],
    });
    window.history.replaceState(
      {},
      "",
      "/models?organization=north&model=north-model",
    );
    render(<App client={client} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(client.modelRuns).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(client.modelRuns).toHaveBeenCalledTimes(1);
  });

  it("opens Canvas node and edge deep links with focused selection", async () => {
    const client = successfulClient();
    const linkedGraph: HeliosGraphDto = {
      ...graph,
      nodes: [
        {
          ...graph.nodes[1],
          id: "dataset:sales.orders",
          label: "Orders",
        },
        {
          ...graph.nodes[1],
          id: "dataset:sales.customers",
          label: "Customers",
        },
      ],
      edges: [
        {
          ...graph.edges[0],
          id: "relationship:orders-customers",
          source: "dataset:sales.orders",
          target: "dataset:sales.customers",
        },
      ],
    };
    vi.mocked(client.modelGraph).mockResolvedValue(linkedGraph);
    vi.mocked(client.modelGraphDetail).mockImplementation(
      async (_modelId, elementId) => ({
        element_type: elementId.startsWith("relationship") ? "edge" : "node",
        id: elementId,
        kind: elementId.startsWith("relationship")
          ? "physical_relationship"
          : "dataset",
        label: elementId.startsWith("relationship") ? null : "Orders",
        source: elementId.startsWith("relationship")
          ? "dataset:sales.orders"
          : null,
        target: elementId.startsWith("relationship")
          ? "dataset:sales.customers"
          : null,
        status: "published",
        confidence: null,
        evidence: "Historical profile evidence",
        details: {},
        available_actions: ["datasource.read"],
      }),
    );
    window.history.replaceState(
      {},
      "",
      "/canvas?organization=north&model=north-model&lens=physical&focus_node_id=dataset%3Asales.orders&element_id=dataset%3Asales.orders",
    );
    const first = render(<App client={client} />);
    expect(
      await screen.findByRole("heading", { name: "Orders" }),
    ).toBeInTheDocument();
    expect(client.modelGraph).toHaveBeenCalledWith(
      "north-model",
      expect.objectContaining({
        lens: "physical",
        focusNodeId: "dataset:sales.orders",
      }),
    );
    await waitFor(() =>
      expect(client.modelGraphDetail).toHaveBeenCalledWith(
        "north-model",
        "dataset:sales.orders",
        undefined,
      ),
    );
    first.unmount();

    window.history.replaceState(
      {},
      "",
      "/canvas?organization=north&model=north-model&lens=physical&focus_node_id=dataset%3Asales.orders&element_id=relationship%3Aorders-customers&related_node_ids=dataset%3Asales.orders%2Cdataset%3Asales.customers",
    );
    render(<App client={client} />);
    expect(
      await screen.findByRole("heading", { name: "physical_relationship" }),
    ).toBeInTheDocument();
    expect(client.modelGraphDetail).toHaveBeenLastCalledWith(
      "north-model",
      "relationship:orders-customers",
      undefined,
    );
    expect(window.location.search).toContain("organization=north");
    expect(window.location.search).toContain("model=north-model");
    expect(document.querySelector('a[href^="/runs/"]')).toBeNull();
  });

  it("renders model-scoped glossary terms and authorized Canvas mappings", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/governance?organization=north&model=north-model",
    );
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", {
        name: "North business glossary",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("A stable identifier for an order."))
      .toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("link", { name: "Order identifier" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Mapped model attributes" }),
    ).toBeInTheDocument();
    expect(client.modelGlossaryTerm).toHaveBeenCalledWith(
      "north-model",
      "order-id",
    );
    expect(screen.getByRole("link", { name: "Show in Canvas" }))
      .toHaveAttribute(
        "href",
        "/canvas?organization=north&model=north-model&lens=physical&focus_node_id=attribute%3Aorders%3Aid&element_id=attribute%3Aorders%3Aid",
      );
  });

  it("creates a glossary term and reconciles the published collection", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/governance?organization=north&model=north-model",
    );
    render(<App client={client} />);
    await screen.findByRole("heading", { name: "North business glossary" });

    fireEvent.click(screen.getByRole("button", { name: "New term" }));
    fireEvent.change(screen.getByLabelText("Business term"), {
      target: { value: "Fulfillment status" },
    });
    fireEvent.change(screen.getByLabelText("Definition"), {
      target: { value: "The governed state of fulfillment." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save term" }));

    await waitFor(() =>
      expect(client.createModelGlossaryTerm).toHaveBeenCalledWith(
        "north-model",
        expect.objectContaining({
          name: "Fulfillment status",
          definition: "The governed state of fulfillment.",
        }),
      ),
    );
    expect(await screen.findByText("Glossary term created."))
      .toBeInTheDocument();
    expect(client.modelGlossaryTerms).toHaveBeenCalledTimes(2);
  });

  it("creates and binds a glossary from the model empty state", async () => {
    const client = successfulClient();
    vi.mocked(client.modelGlossary!)
      .mockResolvedValueOnce({
        model_id: "north-model",
        glossary: null,
        available_actions: ["glossary.read", "glossary.edit"],
      })
      .mockResolvedValue({
        model_id: "north-model",
        glossary: {
          id: "north-glossary",
          name: "North vocabulary",
          description: "Governed business language.",
          term_count: 0,
        },
        available_actions: ["glossary.read", "glossary.edit"],
      });
    vi.mocked(client.createModelGlossary!).mockResolvedValue({
      model_id: "north-model",
      glossary: {
        id: "north-glossary",
        name: "North vocabulary",
        description: "Governed business language.",
        term_count: 0,
      },
      available_actions: ["glossary.read", "glossary.edit"],
    });
    vi.mocked(client.modelGlossaryTerms!).mockResolvedValue({
      model_id: "north-model",
      glossary_id: "north-glossary",
      items: [],
      offset: 0,
      limit: 24,
      total: 0,
      truncated: false,
      available_actions: ["glossary.read", "glossary.edit"],
    });
    window.history.replaceState(
      {},
      "",
      "/governance?organization=north&model=north-model",
    );
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", {
        name: "No glossary is linked to this model",
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create glossary" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "North vocabulary" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Governed business language." },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create glossary",
      }),
    );

    await waitFor(() =>
      expect(client.createModelGlossary).toHaveBeenCalledWith(
        "north-model",
        {
          name: "North vocabulary",
          description: "Governed business language.",
        },
      ),
    );
    expect(
      await screen.findByRole("heading", { name: "North vocabulary" }),
    ).toBeInTheDocument();
  });

  it("reviews proposed glossary terms and links evidence to Canvas", async () => {
    const client = successfulClient();
    vi.mocked(client.modelRunProposals!).mockResolvedValue({
      model_id: "north-model",
      run_id: "run-1",
      section: "glossary_terms",
      items: [
        {
          id: "Order lifecycle",
          section: "glossary_terms",
          proposal: {
            name: "Order lifecycle",
            definition: "The stages of an order.",
            columns: ["sales.orders.status"],
            source: "discovery",
            confidence: 0.91,
          },
          confidence: 0.91,
          provenance: { source: "discovery", llm: null },
          review: { decision: "pending", overrides: null, note: null },
          canvas: {
            review_run_id: "run-1",
            element_id: "concept:Order lifecycle",
            focus_node_id: "concept:Order lifecycle",
            lens: "ontology",
          },
          available_actions: ["accept", "reject", "edit", "view_in_canvas"],
        },
      ],
      page: {
        offset: 0,
        limit: 200,
        returned: 1,
        total: 1,
        has_more: false,
      },
      filters: { decision: null, query: null },
      reviewed_at: null,
      reviewed_by: null,
      available_actions: ["decide"],
    });
    window.history.replaceState(
      {},
      "",
      "/governance/proposals?organization=north&model=north-model",
    );
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Order lifecycle" }),
    ).toBeInTheDocument();
    expect(screen.getByText("91% confidence")).toBeInTheDocument();
    expect(screen.getByText("sales.orders.status")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Canvas" }))
      .toHaveAttribute(
        "href",
        "/canvas?organization=north&model=north-model&review_run_id=run-1&lens=ontology&focus_node_id=concept%3AOrder+lifecycle&element_id=concept%3AOrder+lifecycle",
      );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(client.decideModelProposal).toHaveBeenCalledWith(
        "north-model",
        "run-1",
        expect.objectContaining({
          section: "glossary_terms",
          element_id: "Order lifecycle",
          decision: "accept",
        }),
      ),
    );
  });

  it("creates and continues a persistent MCP-backed conversation", async () => {
    const client = successfulClient();
    const firstConversation = {
      id: "conversation-1",
      model_id: "north-model",
      title: "How many orders?",
      version: 1,
      created_at: "2026-09-24T16:00:00+00:00",
      updated_at: "2026-09-24T16:00:00+00:00",
      messages: [
        {
          id: "user-1",
          role: "user" as const,
          content: "How many orders?",
          created_at: "2026-09-24T16:00:00+00:00",
        },
        {
          id: "assistant-1",
          role: "assistant" as const,
          content: "There are 12 orders.",
          created_at: "2026-09-24T16:00:01+00:00",
        },
      ],
    };
    vi.mocked(client.createModelConversation!).mockResolvedValue({
      conversation: firstConversation,
      turn: {
        model_id: "north-model",
        answer: "There are 12 orders.",
        tool_trace: [
          {
            tool: "run_query",
            arguments: { model: "north-model" },
            result: { columns: ["order_count"], rows: [[12]] },
          },
        ],
        query_result: {
          columns: ["order_count"],
          rows: [[12]],
          sql: "SELECT COUNT(*) FROM sales.orders",
        },
      },
    });
    vi.mocked(client.appendModelConversationTurn!).mockResolvedValue({
      conversation: {
        ...firstConversation,
        version: 2,
        updated_at: "2026-09-24T16:01:00+00:00",
        messages: [
          ...firstConversation.messages,
          {
            id: "user-2",
            role: "user",
            content: "Which region has the most?",
            created_at: "2026-09-24T16:01:00+00:00",
          },
          {
            id: "assistant-2",
            role: "assistant",
            content: "The west region has the most orders.",
            created_at: "2026-09-24T16:01:01+00:00",
          },
        ],
      },
      turn: {
        model_id: "north-model",
        answer: "The west region has the most orders.",
        tool_trace: [],
        query_result: null,
      },
    });
    window.history.replaceState(
      {},
      "",
      "/talk?organization=north&model=north-model",
    );
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "North Model" }),
    ).toBeInTheDocument();
    const composer = screen.getByLabelText("Ask about this model");
    fireEvent.change(composer, { target: { value: "How many orders?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask Helios" }));

    expect(
      await screen.findByText("There are 12 orders."),
    ).toBeInTheDocument();
    expect(screen.getByText("order_count")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(client.createModelConversation).toHaveBeenCalledWith(
      "north-model",
      "How many orders?",
    );
    expect(window.location.search).toContain("conversation=conversation-1");

    fireEvent.change(composer, {
      target: { value: "Which region has the most?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask Helios" }));

    expect(
      await screen.findByText("The west region has the most orders."),
    ).toBeInTheDocument();
    expect(client.appendModelConversationTurn).toHaveBeenCalledWith(
      "north-model",
      "conversation-1",
      "Which region has the most?",
      1,
    );
  });

  it("loads a deep-linked conversation owned by the selected model", async () => {
    const client = successfulClient();
    vi.mocked(client.modelConversations!).mockResolvedValue({
      model_id: "north-model",
      conversations: [
        {
          id: "saved-1",
          model_id: "north-model",
          title: "Saved question",
          version: 1,
          created_at: "2026-09-24T16:00:00+00:00",
          updated_at: "2026-09-24T16:00:00+00:00",
        },
      ],
    });
    vi.mocked(client.modelConversation!).mockResolvedValue({
      id: "saved-1",
      model_id: "north-model",
      title: "Saved question",
      version: 1,
      created_at: "2026-09-24T16:00:00+00:00",
      updated_at: "2026-09-24T16:00:00+00:00",
      messages: [
        {
          id: "saved-user",
          role: "user",
          content: "What metrics are available?",
          created_at: "2026-09-24T16:00:00+00:00",
        },
        {
          id: "saved-assistant",
          role: "assistant",
          content: "Six governed metrics are available.",
          created_at: "2026-09-24T16:00:01+00:00",
        },
      ],
    });
    window.history.replaceState(
      {},
      "",
      "/talk?organization=north&model=north-model&conversation=saved-1",
    );
    render(<App client={client} />);

    expect(
      await screen.findByText("Six governed metrics are available."),
    ).toBeInTheDocument();
    expect(client.modelConversation).toHaveBeenCalledWith(
      "north-model",
      "saved-1",
    );
    expect(
      screen.getByRole("link", { name: "Talk to Your Data" }),
    ).toHaveAttribute(
      "href",
      "/talk?organization=north&model=north-model",
    );
  });

  it("shows conversation authorization failures without crashing the workspace", async () => {
    const client = successfulClient();
    vi.mocked(client.modelConversations!).mockRejectedValue(
      new AuthorizationError("Conversation access was revoked."),
    );
    window.history.replaceState(
      {},
      "",
      "/talk?organization=north&model=north-model",
    );
    render(<App client={client} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You no longer have permission to use this model.",
    );
    expect(screen.getByLabelText("Ask about this model")).toBeInTheDocument();
  });

  it("shows own-session activity and a sanitized event detail", async () => {
    const client = successfulClient();
    window.history.replaceState(
      {},
      "",
      "/activity?organization=north&model=north-model",
    );
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Activity Logs" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Activity Logs" }),
    ).toHaveAttribute(
      "href",
      "/activity?organization=north&model=north-model",
    );
    expect(screen.queryByText("Organization activity")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "GET /api/v1/models/{model_id}",
      }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "GET /api/v1/models/{model_id}",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Sanitized details")).toBeInTheDocument();
    expect(client.auditEvent).toHaveBeenCalledWith("audit-1");
  });

  it("offers organization-wide activity only to organization admins", async () => {
    const client = successfulClient();
    vi.mocked(client.auditEvent!).mockResolvedValue({
      ...(await client.auditEvent!("audit-1")),
      outcome: "error",
      diagnostics: {
        stage: "impala_query",
        exception_chain: [
          {
            exception_type: "OperationalError",
            description: "connection refused",
          },
        ],
      },
    });
    vi.mocked(client.auditEvents!).mockResolvedValue({
      ...(await client.auditEvents!()),
      available_actions: ["audit.read", "audit.read_organization"],
    });
    vi.mocked(client.auditSessions!).mockResolvedValue({
      sessions: [],
      available_actions: ["audit.read", "audit.read_organization"],
    });
    window.history.replaceState(
      {},
      "",
      "/activity?organization=north&model=north-model",
    );
    render(<App client={client} />);

    const scope = await screen.findByLabelText("Organization activity");
    fireEvent.click(scope);
    await waitFor(() =>
      expect(client.auditEvents).toHaveBeenLastCalledWith(
        expect.objectContaining({
          organizationId: "north",
          includeAll: true,
        }),
      ),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "GET /api/v1/models/{model_id}",
      }),
    );
    expect(
      await screen.findByText("Administrator diagnostics"),
    ).toBeInTheDocument();
    expect(screen.getByText(/OperationalError/)).toBeInTheDocument();
  });
});
