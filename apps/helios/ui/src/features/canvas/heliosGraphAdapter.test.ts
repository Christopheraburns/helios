import { describe, expect, it } from "vitest";

import { HeliosGraphDto } from "../../api/client";
import { adaptHeliosGraph } from "./heliosGraphAdapter";
import { adaptCanvasGraphToReactFlow } from "./reactFlowAdapter";

const dto: HeliosGraphDto = {
  model_id: "model-1",
  organization_id: "org-1",
  nodes: [
    node("domain:1", "domain"),
    node("dataset:1", "dataset"),
    node("attribute:1", "attribute"),
    node("custom:1", "custom_kind"),
  ],
  edges: [
    edge(
      "contains:1",
      "physical_relationship",
      "dataset:1",
      "attribute:1",
    ),
    edge("semantic:1", "semantic_relationship", "domain:1", "dataset:1"),
  ],
  summary: {
    node_count: 4,
    edge_count: 2,
    node_kinds: ["attribute", "custom_kind", "dataset", "domain"],
    edge_kinds: ["physical_relationship", "semantic_relationship"],
  },
};

describe("Helios graph adapters", () => {
  it("keeps the frontend model independent and hides attributes by default", () => {
    const graph = adaptHeliosGraph(dto);

    expect(graph.nodes.map((node) => node.id)).toEqual([
      "domain:1",
      "dataset:1",
      "custom:1",
    ]);
    expect(graph.hiddenAttributeCount).toBe(1);
    expect(graph.edges.map((item) => item.id)).toEqual(["semantic:1"]);
    expect(graph.nodes.at(-1)?.category).toBe("generic");
    expect(graph.edges[0].category).toBe("semantic");
  });

  it("reveals only attributes for an explicitly expanded dataset", () => {
    const graph = adaptHeliosGraph(dto, {
      expandedDatasetIds: new Set(["dataset:1"]),
    });

    expect(graph.nodes.map((node) => node.id)).toContain("attribute:1");
    expect(graph.edges.map((item) => item.id)).toContain("contains:1");
    expect(graph.hiddenAttributeCount).toBe(0);
  });

  it("creates renderer-specific nodes only in the React Flow adapter", () => {
    const graph = adaptHeliosGraph(dto);
    const flow = adaptCanvasGraphToReactFlow(graph);

    expect(flow.nodes[0]).toHaveProperty("position");
    expect(flow.nodes[0].data.graphNode).toBe(graph.nodes[0]);
    expect(flow.edges[0].data?.graphEdge).toBe(graph.edges[0]);
  });
});

function node(id: string, kind: string) {
  return {
    id,
    kind,
    label: id,
    status: "published",
    confidence: null,
    evidence: null,
    metadata: {},
    permitted_actions: [],
  };
}

function edge(id: string, kind: string, source: string, target: string) {
  return {
    id,
    kind,
    source,
    target,
    status: "published",
    confidence: null,
    evidence: null,
    metadata: {},
    permitted_actions: [],
  };
}
