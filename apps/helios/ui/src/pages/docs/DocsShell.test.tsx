import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import DocsHubPage from "./DocsHubPage";
import DocsLayout from "./DocsLayout";

describe("DocsLayout", () => {
  it("renders the documentation hub without the Helios API", () => {
    render(
      <MemoryRouter initialEntries={["/docs"]}>
        <Routes>
          <Route path="/docs" element={<DocsLayout />}>
            <Route index element={<DocsHubPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("heading", { name: "Documentation" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Helios Query" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to console" }))
      .toHaveAttribute("href", "/talk");
  });
});
