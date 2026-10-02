import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiUnavailableError, type AssistantWorkspaceState } from "../api/client";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import HomePage from "./HomePage";

const state: AssistantWorkspaceState = {
  organization: { id: "north", name: "North Region" },
  selected_model_id: "north-model",
  llm_provider: { configured: false, provider: null, model: null },
  mcp: { configured: false },
  ontology: { version_count: 2, active_version: "0.2.0" },
  data_sources: [
    {
      id: "warehouse",
      name: "Production Warehouse",
      connector: "impala",
      crawl_enabled: true,
      last_crawl: null,
    },
  ],
  crawl_runs: { total: 3, last: null },
  models: [
    {
      id: "north-model",
      name: "North Model",
      data_source_count: 1,
      lifecycle: {
        publication_state: "proposed",
        discovery_status: "proposals_ready",
        review_status: "pending",
        unresolved_review_items: 4,
        latest_run_id: "run-1",
      },
      summary: {
        dataset_count: 2,
        relationship_count: 1,
        concept_count: 0,
        metric_count: 0,
      },
      has_conversations: false,
      available_actions: ["model.read"],
    },
  ],
  journeys: [
    {
      id: "semantic-model",
      title: "Create a semantic model",
      description: "From connected sources to a published model.",
      progress: { done: 1, total: 3 },
      next_step_id: "discover",
      steps: [
        {
          id: "sources",
          title: "Connect a data source",
          description: "Register the warehouse.",
          route: "/data-sources",
          params: {},
          doc_slug: "data-sources",
          status: "done",
          note: null,
        },
        {
          id: "discover",
          title: "Run discovery",
          description: "Harvest and profile the sources.",
          route: "/models",
          params: {},
          doc_slug: null,
          status: "external",
          note: "Discovery runs as a Workbench Job.",
        },
        {
          id: "review",
          title: "Review proposals",
          description: "Accept or reject proposed elements.",
          route: "/canvas",
          params: { review_run_id: "run-1" },
          doc_slug: null,
          status: "todo",
          note: null,
        },
      ],
    },
    {
      id: "ask",
      title: "Ask your data",
      description: "Set up an LLM provider and chat.",
      progress: { done: 0, total: 1 },
      next_step_id: "provider",
      steps: [
        {
          id: "provider",
          title: "Configure an LLM provider",
          description: "Choose the provider used by Talk to Your Data.",
          route: "/governance/model-provider",
          params: {},
          doc_slug: null,
          status: "todo",
          note: null,
        },
      ],
    },
  ],
};

function renderPage(
  overrides: Partial<ApplicationContextState> = {},
  onOpenAssistant = vi.fn(),
) {
  const context = {
    status: "ready",
    organizations: [{ id: "north", name: "North Region", available_actions: [] }],
    selectedOrganizationId: "north",
    selectedModelId: "north-model",
    loadAssistantWorkspaceState: vi.fn().mockResolvedValue(state),
    ...overrides,
  } as unknown as ApplicationContextState;
  render(
    <MemoryRouter initialEntries={["/home?organization=north&model=north-model"]}>
      <HomePage context={context} onOpenAssistant={onOpenAssistant} />
    </MemoryRouter>,
  );
  return { context, onOpenAssistant };
}

describe("HomePage", () => {
  it("renders journeys with the next step highlighted and context-aware links", async () => {
    const { context } = renderPage();

    expect(
      await screen.findByRole("heading", { name: "Create a semantic model" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask your data" }))
      .toBeInTheDocument();
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
    expect(context.loadAssistantWorkspaceState).toHaveBeenCalledWith(
      "north",
      "north-model",
    );

    const steps = screen.getAllByRole("listitem").filter((item) =>
      item.classList.contains("journey-step"),
    );
    expect(steps).toHaveLength(4);
    const discover = steps[1];
    expect(within(discover).getByText("Next")).toBeInTheDocument();
    expect(discover).toHaveClass("journey-step--next");
    expect(within(discover).getByText("Discovery runs as a Workbench Job."))
      .toBeInTheDocument();
    expect(within(discover).getByRole("img", { name: "Outside Helios" }))
      .toBeInTheDocument();
    expect(within(steps[0]).queryByText("Next")).not.toBeInTheDocument();
    expect(within(steps[0]).getByRole("img", { name: "Done" }))
      .toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Open Review proposals" }))
      .toHaveAttribute(
        "href",
        "/canvas?organization=north&model=north-model&review_run_id=run-1",
      );
    expect(screen.getByRole("link", { name: "Open Connect a data source" }))
      .toHaveAttribute("href", "/data-sources?organization=north&model=north-model");
    expect(
      screen.getByRole("link", { name: "Read guide for Connect a data source" }),
    ).toHaveAttribute("href", "/docs/helios/data-sources");
  });

  it("summarises workspace status and lists models with their lifecycle", async () => {
    renderPage();

    expect(await screen.findByText("Not configured")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configure an LLM provider" }))
      .toHaveAttribute(
        "href",
        "/governance/model-provider?organization=north&model=north-model",
      );
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getByText("3 crawl runs")).toBeInTheDocument();
    expect(screen.getByText("North Model")).toBeInTheDocument();
    expect(screen.getByText("Proposed")).toHaveClass("lifecycle-badge--proposed");
    expect(screen.getByText("4 unresolved")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open North Model overview" }))
      .toHaveAttribute("href", "/model-overview?organization=north&model=north-model");
  });

  it("opens the assistant from the header action", async () => {
    const { onOpenAssistant } = renderPage();
    await screen.findByRole("heading", { name: "Create a semantic model" });

    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));

    expect(onOpenAssistant).toHaveBeenCalledTimes(1);
  });

  it("explains when the API does not expose workspace state", async () => {
    renderPage({
      loadAssistantWorkspaceState: vi.fn(() => {
        throw new ApiUnavailableError(
          "The assistant workspace state API is unavailable.",
        );
      }),
    });

    expect(
      await screen.findByRole("heading", { name: "Workspace state unavailable" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an error state with retry when workspace state fails", async () => {
    const loadAssistantWorkspaceState = vi
      .fn()
      .mockRejectedValueOnce(new Error("Workspace state failed."))
      .mockResolvedValueOnce(state);
    renderPage({ loadAssistantWorkspaceState });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Workspace state failed.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "Create a semantic model" }),
    ).toBeInTheDocument();
  });
});
