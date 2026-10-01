import { describe, expect, it } from "vitest";
import type { OntologyGraphPayload } from "../../api/client";
import {
  brokenClasses,
  buildOntologyGraphModel,
  COLUMN_WIDTH,
  mappingsFor,
  neighbourhood,
} from "./ontologyGraphModel";

const cls = (key: string, layer = "core", abstract = false) => ({
  label: "Class",
  key,
  properties: { layer, kind: "entity", abstract, description: `${key} desc` },
});
const isA = (child: string, parent: string) => ({
  type: "IS_A",
  from_label: "Class",
  from_key: child,
  to_label: "Class",
  to_key: parent,
  properties: {},
});
const attr = (owner: string, slot: string, declaredBy: string, inherited: boolean) => ({
  label: "Attribute",
  key: `${owner}#${slot}`,
  properties: { slot_name: slot, declared_by: declaredBy, inherited, multivalued: true },
});
const range = (owner: string, slot: string, target: string) => ({
  type: "RANGE",
  from_label: "Attribute",
  from_key: `${owner}#${slot}`,
  to_label: "Class",
  to_key: target,
  properties: {},
});

const payload: OntologyGraphPayload = {
  version: "0.1.0",
  content_hash: "x",
  nodes: [
    cls("Thing", "core", true),
    cls("Person"),
    cls("Organization"),
    cls("Customer", "pack"),
    cls("Segment"),
    attr("Person", "affiliated_with", "Person", false),
    attr("Customer", "affiliated_with", "Person", true),
  ],
  edges: [
    isA("Person", "Thing"),
    isA("Organization", "Thing"),
    isA("Customer", "Person"),
    range("Person", "affiliated_with", "Organization"),
    range("Customer", "affiliated_with", "Organization"),
  ],
};

describe("buildOntologyGraphModel", () => {
  const model = buildOntologyGraphModel(payload);
  const byName = Object.fromEntries(model.classes.map((c) => [c.name, c]));

  it("lays the is_a forest out left to right, parents centred on their children", () => {
    expect(byName.Thing.depth).toBe(0);
    expect(byName.Customer.x).toBe(2 * COLUMN_WIDTH);
    const kids = [byName.Person.y, byName.Organization.y];
    expect(byName.Thing.y).toBe((Math.min(...kids) + Math.max(...kids)) / 2);
    // Segment has no parent: it is a separate root below the Thing tree.
    expect(byName.Segment.depth).toBe(0);
    expect(byName.Segment.y).toBeGreaterThan(byName.Organization.y);
  });

  it("keeps layer and abstract flags from the payload", () => {
    expect(byName.Customer.layer).toBe("pack");
    expect(byName.Thing.abstract).toBe(true);
  });

  it("draws an inherited attribute once, from the class that declares it", () => {
    const ranges = model.links.filter((l) => l.kind === "range");
    expect(ranges).toHaveLength(1);
    expect(ranges[0]).toMatchObject({ source: "Person", target: "Organization", label: "affiliated_with" });
  });

  it("finds a class's neighbourhood with or without ranges", () => {
    expect(neighbourhood(model, "Person", false)).toEqual(new Set(["Person", "Thing", "Customer"]));
    expect(neighbourhood(model, "Person", true).has("Organization")).toBe(true);
  });
});

describe("mappings", () => {
  const withMappings: OntologyGraphPayload = {
    ...payload,
    nodes: [
      ...payload.nodes,
      { label: "OssieElement", key: "customer", properties: { kind: "dataset", status: "ok" } },
      { label: "OssieElement", key: "gone", properties: { kind: "dataset", status: "not_found" } },
    ],
    edges: [
      ...payload.edges,
      {
        type: "MAPS_TO",
        from_label: "OssieElement",
        from_key: "customer",
        to_label: "Class",
        to_key: "Customer",
        properties: { primary: ["c_customer_sk"], secondary: ["c_customer_id"], display: [], aliases: [] },
      },
      {
        type: "MAPS_TO",
        from_label: "OssieElement",
        from_key: "gone",
        to_label: "Class",
        to_key: "Person",
        properties: { primary: ["x"] },
      },
    ],
  };

  it("lists a class's Ossie mappings with their identifiers", () => {
    expect(mappingsFor(withMappings, "Customer")).toEqual([
      {
        element: "customer",
        kind: "dataset",
        status: "ok",
        primary: ["c_customer_sk"],
        secondary: ["c_customer_id"],
        display: [],
        aliases: [],
      },
    ]);
  });

  it("flags classes whose mapped element is missing from the model", () => {
    expect(brokenClasses(withMappings)).toEqual(new Set(["Person"]));
  });
});
