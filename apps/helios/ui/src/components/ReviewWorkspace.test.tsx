import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";

import type { ProposalCollectionOptions, ReviewSection } from "../api/client";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import ReviewWorkspace from "./ReviewWorkspace";

const counts = (pending: number) => ({
  accept: 0,
  reject: 0,
  edit: 0,
  pending,
  total: pending,
});

function fakeContext() {
  const loadModelRunProposals = vi.fn(
    async (_model: string, runId: string, options: ProposalCollectionOptions) => ({
      model_id: "m",
      run_id: runId,
      section: options.section,
      items: [],
      page: { offset: 0, limit: 25, returned: 0, total: 0, has_more: false },
      filters: { decision: options.decision ?? null, query: null },
      reviewed_at: null,
      reviewed_by: null,
      available_actions: [],
    }),
  );
  const sections: Record<ReviewSection, ReturnType<typeof counts>> = {
    datasets: counts(0),
    fields: counts(0),
    relationships: counts(3),
    metrics: counts(1),
    glossary_terms: counts(0),
  };
  const context = {
    selectedModelId: "m",
    loadModelRunProposals,
    loadModelReview: vi.fn(async () => ({
      model_id: "m",
      run_id: "run-1",
      sections,
      reviewed_at: null,
      reviewed_by: null,
      preflight_issues: {},
      validation_errors: [],
      publish_ready: false,
      publication: null,
      available_actions: [],
    })),
    refreshModelOverview: vi.fn(),
  } as unknown as ApplicationContextState;
  return { context, loadModelRunProposals };
}

it("opens on the first section with pending items when asked to focus on them", async () => {
  const { context, loadModelRunProposals } = fakeContext();
  render(
    <MemoryRouter initialEntries={["/models/runs/run-1?review_focus=pending"]}>
      <ReviewWorkspace context={context} runId="run-1" />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Relationships/ }),
    ).toHaveAttribute("aria-pressed", "true"),
  );
  expect(screen.getByLabelText("Decision")).toHaveValue("pending");
  await waitFor(() =>
    expect(loadModelRunProposals).toHaveBeenLastCalledWith(
      "m",
      "run-1",
      expect.objectContaining({ section: "relationships", decision: "pending" }),
    ),
  );
});

it("opens on datasets with no filter by default", async () => {
  const { context } = fakeContext();
  render(
    <MemoryRouter initialEntries={["/models/runs/run-1"]}>
      <ReviewWorkspace context={context} runId="run-1" />
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("button", { name: /Datasets/ }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("Decision")).toHaveValue("");
});
