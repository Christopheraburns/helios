import {
  HeliosGraphDto,
  HeliosGraphEdgeDto,
  HeliosGraphNodeDto,
} from "../../api/client";
import {
  CanvasGraph,
  CanvasGraphEdge,
  CanvasGraphNode,
  CanvasNodeCategory,
  CanvasRelationshipCategory,
} from "./graphModel";

const LARGE_GRAPH_NODE_THRESHOLD = 250;
const LARGE_GRAPH_EDGE_THRESHOLD = 500;

export interface GraphAdapterOptions {
  expandedDatasetIds?: ReadonlySet<string>;
}

export function adaptHeliosGraph(
  dto: HeliosGraphDto,
  options: GraphAdapterOptions = {},
): CanvasGraph {
  const expandedDatasetIds = options.expandedDatasetIds ?? new Set<string>();
  const attributeIds = new Set(
    dto.nodes.filter((node) => node.kind === "attribute").map((node) => node.id),
  );
  const visibleAttributeIds = new Set<string>();

  for (const edge of dto.edges) {
    if (
      expandedDatasetIds.has(edge.source) &&
      attributeIds.has(edge.target)
    ) {
      visibleAttributeIds.add(edge.target);
    }
    if (
      expandedDatasetIds.has(edge.target) &&
      attributeIds.has(edge.source)
    ) {
      visibleAttributeIds.add(edge.source);
    }
  }

  const nodes = dto.nodes
    .filter(
      (node) =>
        node.kind !== "attribute" || visibleAttributeIds.has(node.id),
    )
    .map(adaptNode);
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges = dto.edges
    .filter(
      (edge) =>
        visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
    )
    .map(adaptEdge);

  return {
    modelId: dto.model_id,
    organizationId: dto.organization_id,
    nodes,
    edges,
    sourceNodeCount:
      dto.navigation?.authorized_node_count ?? dto.summary.node_count,
    sourceEdgeCount:
      dto.navigation?.authorized_edge_count ?? dto.summary.edge_count,
    hiddenAttributeCount: attributeIds.size - visibleAttributeIds.size,
    isLarge:
      (dto.navigation?.authorized_node_count ?? dto.summary.node_count) >
        LARGE_GRAPH_NODE_THRESHOLD ||
      (dto.navigation?.authorized_edge_count ?? dto.summary.edge_count) >
        LARGE_GRAPH_EDGE_THRESHOLD,
  };
}

function adaptNode(node: HeliosGraphNodeDto): CanvasGraphNode {
  return {
    id: node.id,
    kind: node.kind,
    category: nodeCategory(node.kind),
    label: node.label,
    status: node.status,
    confidence: node.confidence,
    evidence: node.evidence,
    metadata: node.metadata,
    permittedActions: node.permitted_actions,
  };
}

function adaptEdge(edge: HeliosGraphEdgeDto): CanvasGraphEdge {
  return {
    id: edge.id,
    kind: edge.kind,
    category: relationshipCategory(edge.kind),
    source: edge.source,
    target: edge.target,
    status: edge.status,
    confidence: edge.confidence,
    evidence: edge.evidence,
    metadata: edge.metadata,
    permittedActions: edge.permitted_actions,
  };
}

function nodeCategory(kind: string): CanvasNodeCategory {
  return ["domain", "concept", "dataset", "attribute", "metric"].includes(kind)
    ? (kind as CanvasNodeCategory)
    : "generic";
}

function relationshipCategory(kind: string): CanvasRelationshipCategory {
  const normalized = kind.toLowerCase();
  if (normalized.includes("physical")) return "physical";
  if (normalized.includes("inferred")) return "inferred";
  if (normalized.includes("semantic")) return "semantic";
  if (normalized.includes("ontology")) return "ontology";
  return "generic";
}
