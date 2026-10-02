import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import {
  ApiUnavailableError,
  AssistantTurnResponse,
  ServiceUnavailableError,
} from "../api/client";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import AssistantDrawer from "./AssistantDrawer";

const turn: AssistantTurnResponse = {
  answer: "Open the canvas in review mode.\nThere are 2 pending items.",
  actions: [
    {
      type: "navigate",
      route: "/canvas",
      params: { review_run_id: "run-1" },
      label: "Review proposals",
    },
  ],
  tool_trace: [],
  provenance: { llm: { provider: "openai", model: "gpt-4o" } },
  request_id: "req-1",
  trace_run_id: null,
};

function renderDrawer(overrides: Partial<ApplicationContextState> = {}) {
  const onClose = vi.fn();
  const context = {
    status: "ready",
    selectedOrganizationId: "north",
    selectedModelId: "north-model",
    loadAssistantWorkspaceState: vi.fn().mockResolvedValue({
      journeys: [
        {
          id: "semantic-model",
          title: "Create a semantic model",
          description: "",
          progress: { done: 1, total: 3 },
          next_step_id: "review",
          steps: [
            {
              id: "review",
              title: "Review proposals",
              description: "",
              route: "/canvas",
              params: {},
              doc_slug: null,
              status: "todo",
              note: null,
            },
          ],
        },
      ],
    }),
    sendAssistantTurn: vi.fn().mockResolvedValue(turn),
    ...overrides,
  } as unknown as ApplicationContextState;
  window.history.replaceState({}, "", "/home?organization=north&model=north-model");
  render(
    <BrowserRouter>
      <AssistantDrawer context={context} open onClose={onClose} />
    </BrowserRouter>,
  );
  return { context, onClose };
}

describe("AssistantDrawer", () => {
  it("sends a message, renders the answer with actions, and navigates", async () => {
    const { context } = renderDrawer();

    expect(screen.getByRole("complementary", { name: "Navigation assistant" }))
      .toBeInTheDocument();
    expect(await screen.findByText("1 of 3")).toBeInTheDocument();
    expect(screen.getByText("Next: Review proposals")).toBeInTheDocument();
    const input = screen.getByLabelText("Ask the assistant");
    expect(input).toHaveFocus();

    fireEvent.change(input, { target: { value: "What should I do next?" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(context.sendAssistantTurn).toHaveBeenCalledWith({
      organization: "north",
      model: "north-model",
      message: "What should I do next?",
      history: [],
      location: {
        pathname: "/home",
        search: "?organization=north&model=north-model",
      },
    });
    expect(
      await screen.findByText(/Open the canvas in review mode/),
    ).toHaveTextContent("There are 2 pending items.");
    expect(screen.getByText("via openai/gpt-4o")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Review proposals" }));

    expect(window.location.pathname).toBe("/canvas");
    expect(window.location.search).toBe(
      "?organization=north&model=north-model&review_run_id=run-1",
    );
    await waitFor(() =>
      expect(context.loadAssistantWorkspaceState).toHaveBeenCalledTimes(2),
    );
  });

  it("sends suggestion chips and the recent transcript as history", async () => {
    const { context } = renderDrawer();
    await screen.findByText("1 of 3");

    fireEvent.click(screen.getByRole("button", { name: "Explain this page" }));
    await screen.findByText(/Open the canvas in review mode/);
    expect(
      screen.queryByRole("button", { name: "What should I do next?" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Ask the assistant"), {
      target: { value: "And then?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(context.sendAssistantTurn).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: "And then?",
          history: [
            { role: "user", content: "Explain this page" },
            { role: "assistant", content: turn.answer },
          ],
        }),
      ),
    );
  });

  it("points to the LLM provider settings when the assistant is unavailable", async () => {
    renderDrawer({
      sendAssistantTurn: vi
        .fn()
        .mockRejectedValue(new ServiceUnavailableError("No provider configured.")),
    });

    fireEvent.change(screen.getByLabelText("Ask the assistant"), {
      target: { value: "Hello" },
    });
    fireEvent.keyDown(screen.getByLabelText("Ask the assistant"), { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The assistant needs an LLM provider. Configure one under Settings › LLM Provider.",
    );
    expect(screen.getByRole("link", { name: "Open LLM Provider settings" }))
      .toHaveAttribute(
        "href",
        "/governance/model-provider?organization=north&model=north-model",
      );
  });

  it("shows other errors verbatim and closes on Escape", async () => {
    const { onClose } = renderDrawer({
      sendAssistantTurn: vi
        .fn()
        .mockRejectedValue(new ApiUnavailableError("The assistant timed out.")),
    });

    fireEvent.change(screen.getByLabelText("Ask the assistant"), {
      target: { value: "Hello" },
    });
    fireEvent.keyDown(screen.getByLabelText("Ask the assistant"), { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The assistant timed out.",
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
