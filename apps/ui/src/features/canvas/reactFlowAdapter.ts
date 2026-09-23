import {
  Edge,
  MarkerType,
  Node,
  Position,
} from "@xyflow/react";

import { CanvasGraph, CanvasGraphEdge, CanvasGraphNode } from "./graphModel";

export type HeliosFlowNodeData = {
  graphNode: CanvasGraphNode;
};

export type HeliosFlowEdgeData = {
  graphEdge: CanvasGraphEdge;
};

export type HeliosFlowNode = Node<HeliosFlowNodeData, "helios">;
export type HeliosFlowEdge = Edge<HeliosFlowEdgeData>;

export interface ReactFlowGraph {
  nodes: HeliosFlowNode[];
  edges: HeliosFlowEdge[];
}

const categoryOrder = [
  "domain",
  "concept",
  "dataset",
  "metric",
  "attribute",
  "generic",
] as const;

export function adaptCanvasGraphToReactFlow(
  graph: CanvasGraph,
): ReactFlowGraph {
  const grouped = new Map<string, CanvasGraphNode[]>();
  for (const node of graph.nodes) {
    const group = grouped.get(node.category) ?? [];
    group.push(node);
    grouped.set(node.category, group);
  }

  const flowNodes: HeliosFlowNode[] = [];
  categoryOrder.forEach((category, column) => {
    const nodes = grouped.get(category) ?? [];
    nodes
      .sort((left, right) => left.label.localeCompare(right.label))
      .forEach((node, row) => {
        flowNodes.push({
          id: node.id,
          type: "helios",
          position: {
            x: column * 280,
            y: row * 112 + (column % 2) * 36,
          },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: { graphNode: node },
          ariaLabel: `${node.kind}: ${node.label}`,
        });
      });
  });

  return {
    nodes: flowNodes,
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      data: { graphEdge: edge },
      className: `canvas-edge canvas-edge--${edge.category} canvas-edge--state-${edge.status.toLowerCase().replaceAll("_", "-")}`,
      label:
        edge.status === "published"
          ? undefined
          : `${formatState(edge.status)}${
              edge.confidence !== null
                ? ` · ${Math.round(edge.confidence * 100)}%`
                : ""
            }`,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 14,
        height: 14,
      },
      selectable: true,
    })),
  };
}

function formatState(status: string): string {
  return status
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
