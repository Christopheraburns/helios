import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HeliosApi } from "../../api/client";
import PublishPanel from "./PublishPanel";

const SCHEMAS = [
  { schema_path: "ontology/customers/example-tenant/extension.yaml", name: "ext", title: "Example Tenant Extension", version: "0.1.0", layer: "customer" },
  { schema_path: "ontology/packs/retail/retail.yaml", name: "retail", title: "Helios Retail Domain Pack", version: "0.2.0", layer: "pack" },
];

function check(status: "new" | "identical" | "conflict", broken: Array<Record<string, string>> = []) {
  return {
    version: "0.2.0", content_hash: "h", node_count: 400, edge_count: 500, enum_count: 4, class_count: 48,
    schema_path: SCHEMAS[0].schema_path, broken_mappings: broken, status, existing_content_hash: null,
    changes: { compared_with: "0.1.9", classes_added: ["Sale"], classes_removed: [], mappings_added: ["item → Brand"],
               mappings_removed: [], attributes_added: 7, attributes_removed: 0 },
    lakehouse: true,
  };
}

function setup(api: Partial<HeliosApi>, organizationId = "org1") {
  const onPublished = vi.fn();
  render(
    <PublishPanel api={() => api as HeliosApi} organizationId={organizationId} onPublished={onPublished} onClose={() => {}} />,
  );
  return onPublished;
}

describe("PublishPanel", () => {
  it("checks, publishes and activates", async () => {
    const api = {
      ontologySchemas: vi.fn().mockResolvedValue(SCHEMAS),
      checkOntology: vi.fn().mockResolvedValue(check("new")),
      publishOntology: vi.fn().mockResolvedValue({ version: "0.2.0" }),
      activateOntology: vi.fn().mockResolvedValue({ version: "0.2.0" }),
    };
    const onPublished = setup(api);
    const version = (await screen.findByPlaceholderText("e.g. 0.2.0")) as HTMLInputElement;
    expect(version.value).toBe("0.1.0"); // prefilled from the chosen schema
    fireEvent.change(version, { target: { value: "0.2.0" } });
    fireEvent.click(screen.getByText("Check"));
    expect(await screen.findByText("new version")).toBeTruthy();
    expect(screen.getByText(/Classes added: Sale/)).toBeTruthy();
    expect(api.checkOntology).toHaveBeenCalledWith("0.2.0", SCHEMAS[0].schema_path, "org1");
    fireEvent.click(screen.getByText("Publish 0.2.0"));
    fireEvent.click(await screen.findByText("Activate 0.2.0"));
    expect(await screen.findByText("Active.")).toBeTruthy();
    expect(api.activateOntology).toHaveBeenCalledWith("0.2.0", "org1");
    expect(onPublished).toHaveBeenCalledWith("0.2.0");
  });

  it("refuses to publish a conflicting version and shows broken mappings", async () => {
    const api = {
      ontologySchemas: vi.fn().mockResolvedValue(SCHEMAS),
      checkOntology: vi.fn().mockResolvedValue(check("conflict", [{ class: "", mapped_to: "reason", status: "not_found" }])),
      publishOntology: vi.fn(),
    };
    setup(api);
    await screen.findByPlaceholderText("e.g. 0.2.0");
    fireEvent.click(screen.getByText("Check"));
    expect(await screen.findByText("version number taken by different content")).toBeTruthy();
    expect(screen.getByText(/reason: not_found/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Publish/ })).toBeNull();
    expect(api.publishOntology).not.toHaveBeenCalled();
  });

  it("asks for an organization first", () => {
    setup({ ontologySchemas: vi.fn().mockResolvedValue(SCHEMAS) }, "");
    expect(screen.getByText(/Select an organization first/)).toBeTruthy();
  });
});
