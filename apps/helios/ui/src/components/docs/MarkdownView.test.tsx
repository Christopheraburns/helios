import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MarkdownView, { headingSlugFromText } from "./MarkdownView";

vi.mock("./MermaidBlock", () => ({
  default: ({ chart }: { chart: string }) => (
    <div data-testid="mermaid">{chart}</div>
  ),
}));

describe("MarkdownView", () => {
  it("slugifies heading text for anchors", () => {
    expect(headingSlugFromText("Hello, World!")).toBe("hello-world");
  });

  it("renders headings with slug ids", () => {
    render(<MarkdownView markdown={"## Section One\n\nBody text."} />);
    const heading = screen.getByRole("heading", { name: "Section One" });
    expect(heading).toHaveAttribute("id", "section-one");
    expect(screen.getByText("Body text.")).toBeInTheDocument();
  });

  it("routes mermaid fences to the diagram component", () => {
    render(
      <MarkdownView
        markdown={"```mermaid\nflowchart LR\n  A-->B\n```"}
      />,
    );
    expect(screen.getByTestId("mermaid")).toHaveTextContent("flowchart LR");
  });
});
