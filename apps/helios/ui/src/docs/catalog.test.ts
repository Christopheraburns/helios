import { describe, expect, it } from "vitest";

import { docManifest, docsForProject, searchDocs } from "./catalog";

describe("docs catalog", () => {
  it("loads a non-empty manifest from the sync script", () => {
    expect(docManifest.length).toBeGreaterThan(0);
  });

  it("groups Helios Query pages by project", () => {
    const helios = docsForProject("helios");
    expect(helios.some((entry) => entry.slug === "walkthrough")).toBe(true);
    expect(helios.every((entry) => entry.project === "helios")).toBe(true);
  });

  it("searches titles and excerpts", () => {
    const results = searchDocs("MCP");
    expect(results.some((entry) => entry.slug === "mcp-server")).toBe(true);
  });
});
