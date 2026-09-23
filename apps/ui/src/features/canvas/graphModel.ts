export type CanvasNodeCategory =
  | "domain"
  | "concept"
  | "dataset"
  | "attribute"
  | "metric"
  | "generic";

export type CanvasRelationshipCategory =
  | "physical"
  | "inferred"
  | "semantic"
  | "ontology"
  | "generic";

export interface CanvasGraphNode {
  id: string;
  kind: string;
  category: CanvasNodeCategory;
  label: string;
  status: string;
  confidence: number | null;
  evidence: string | null;
  metadata: Record<string, unknown>;
  permittedActions: string[];
}

export interface CanvasGraphEdge {
  id: string;
  kind: string;
  category: CanvasRelationshipCategory;
  source: string;
  target: string;
  status: string;
  confidence: number | null;
  evidence: string | null;
  metadata: Record<string, unknown>;
  permittedActions: string[];
}

export interface CanvasGraph {
  modelId: string;
  organizationId: string;
  nodes: CanvasGraphNode[];
  edges: CanvasGraphEdge[];
  sourceNodeCount: number;
  sourceEdgeCount: number;
  hiddenAttributeCount: number;
  isLarge: boolean;
}
