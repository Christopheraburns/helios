import { describe, expect, it } from "vitest";
import { inputFrom, scopeSummary } from "./DataSourcesPage";

describe("data source form", () => {
  it("turns object-store fields into a scope", () => {
    const input = inputFrom({
      name: " Contracts ", description: "", connector: "object_store", connection_ref: "S3 Object Store",
      enabled: true, settings_version: "", bucket: "b", prefix: "contracts/", include: "*.pdf, *.eml",
      exclude: "", max_bytes: "1000", max_objects: "50",
    });
    expect(input.name).toBe("Contracts");
    expect(input.scope).toEqual({
      bucket: "b", prefix: "contracts/", include: ["*.pdf", "*.eml"], exclude: [], max_bytes: 1000, max_objects: 50,
    });
    expect(input.crawl).toEqual({ enabled: true, settings_version: null, schedule: "manual" });
  });

  it("turns table-rows fields into a scope with filters", () => {
    const input = inputFrom({
      name: "Tickets", description: "", connector: "table_rows", connection_ref: "impala", enabled: false,
      settings_version: "3", table: "support.tickets", key_columns: "id", text_columns: "subject, body",
      timestamp_column: "", filters: "region=west, status=open", max_rows: "100",
    });
    expect(input.scope).toEqual({
      table: "support.tickets", key_columns: ["id"], text_columns: ["subject", "body"], timestamp_column: null,
      filters: { region: "west", status: "open" }, max_rows: 100,
    });
    expect(input.crawl).toMatchObject({ enabled: false, settings_version: 3 });
  });

  it("summarises scopes", () => {
    expect(scopeSummary({ connector: "object_store", scope: { bucket: "b", prefix: "p/" } })).toBe("s3://b/p/");
    expect(scopeSummary({ connector: "impala", scope: {} })).toBe("used by semantic models");
  });
});
