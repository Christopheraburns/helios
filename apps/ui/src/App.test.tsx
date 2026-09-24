import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  ApiDiagnostics,
  AuthenticationError,
  HeliosApi,
  HeliosGraphDto,
  ModelOverview,
  ModelSystemStatus,
  ModelsResponse,
  OrganizationsResponse,
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
    applicationUrl: vi.fn(
      (path: string) => `https://helios-api.example.test${path}`,
    ),
  };
}

describe("Helios application shell", () => {
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
    expect(document.querySelector('a[href*="/runs/"]')).toBeNull();
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
    expect(screen.queryByText("Semantic model")).not.toBeInTheDocument();
    expect(screen.queryByText(/Explore the authorized domains/i))
      .not.toBeInTheDocument();
    expect(document.getElementById("main-content"))
      .toHaveClass("app__main--canvas");
    expect(screen.queryByText("Nothing selected")).not.toBeInTheDocument();
    expect(await screen.findByText(/1 attributes are hidden/i))
      .toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Physical" }))
      .toHaveAttribute("aria-pressed", "true");
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
});
