import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  ApiDiagnostics,
  AuthenticationError,
  HeliosApi,
  HeliosGraphDto,
  ModelOverview,
  ModelsResponse,
  OrganizationsResponse,
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
      modelGraph: vi.fn(() => pending),
      modelGraphDetail: vi.fn(() => pending),
    };

    render(<App client={client} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading Helios");
    expect(screen.getByLabelText("Organization")).toBeDisabled();
    expect(screen.getByLabelText("Model")).toBeDisabled();
  });

  it("loads API-backed selectors, account identity, and model overview", async () => {
    render(<App client={successfulClient()} />);

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
        "https://helios-api.example.test/runs/run-1/review",
      );
    expect(screen.queryByRole("link", { name: "Publish" }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary navigation" }))
      .toHaveTextContent("Governance");
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
