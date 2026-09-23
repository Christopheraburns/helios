import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  ApiDiagnostics,
  AuthenticationError,
  HeliosApi,
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

function successfulClient(): HeliosApi {
  return {
    health: vi.fn().mockResolvedValue({ status: "ok" }),
    diagnostics: vi.fn().mockResolvedValue(diagnostics),
    organizations: vi.fn().mockResolvedValue(organizations),
    models: vi.fn((organizationId: string) =>
      Promise.resolve(modelsFor(organizationId)),
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
    };

    render(<App client={client} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading Helios");
    expect(screen.getByLabelText("Organization")).toBeDisabled();
    expect(screen.getByLabelText("Model")).toBeDisabled();
  });

  it("loads API-backed selectors, account identity, and model overview", async () => {
    render(<App client={successfulClient()} />);

    expect(
      await screen.findByRole("heading", { name: "Overview" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Organization")).toHaveValue("north");
    expect(await screen.findByLabelText("Model")).toHaveValue("north-model");
    expect(screen.getByText("Data Analyst")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "North Model" }),
    ).toBeInTheDocument();
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

  it("renders authentication errors without fabricating workspace data", async () => {
    const client: HeliosApi = {
      health: vi.fn().mockResolvedValue({ status: "ok" }),
      diagnostics: vi
        .fn()
        .mockRejectedValue(new AuthenticationError("Sign in required.")),
      organizations: vi.fn().mockResolvedValue(organizations),
      models: vi.fn().mockResolvedValue(modelsFor("north")),
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

  it("uses placeholder routes without implementing canvas behavior", async () => {
    render(<App client={successfulClient()} />);
    await screen.findByRole("heading", { name: "Overview" });

    fireEvent.click(screen.getByRole("link", { name: "Canvas" }));

    expect(
      await screen.findByRole("heading", { name: "Canvas" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/ready for the next phase/i)).toBeInTheDocument();
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
