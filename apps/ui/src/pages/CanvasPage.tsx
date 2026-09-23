import {
  Background,
  Controls,
  EdgeMouseHandler,
  NodeMouseHandler,
  NodeTypes,
  ReactFlow,
  ReactFlowInstance,
  Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";

import {
  ApiUnavailableError,
  AuthenticationError,
  AuthorizationError,
  GraphElementDetail,
  GraphLens,
  HeliosGraphDto,
  HeliosGraphNodeDto,
} from "../api/client";
import { EmptyState, ErrorState } from "../components/AsyncState";
import HeliosNode from "../features/canvas/HeliosNode";
import { adaptHeliosGraph } from "../features/canvas/heliosGraphAdapter";
import {
  CanvasGraphEdge,
  CanvasGraphNode,
} from "../features/canvas/graphModel";
import {
  adaptCanvasGraphToReactFlow,
  HeliosFlowEdge,
  HeliosFlowNode,
} from "../features/canvas/reactFlowAdapter";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface CanvasPageProps {
  context: ApplicationContextState;
}

type CanvasStatus = "idle" | "loading" | "ready" | "error";

const nodeTypes: NodeTypes = { helios: HeliosNode };

export default function CanvasPage({ context }: CanvasPageProps) {
  const [status, setStatus] = useState<CanvasStatus>("idle");
  const [dto, setDto] = useState<HeliosGraphDto>();
  const [errorMessage, setErrorMessage] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const [expandedDatasetIds, setExpandedDatasetIds] = useState<Set<string>>(
    new Set(),
  );
  const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(
    new Set(),
  );
  const [expansionChildren, setExpansionChildren] = useState<
    Map<string, Set<string>>
  >(new Map());
  const [selectedNode, setSelectedNode] = useState<CanvasGraphNode>();
  const [selectedEdge, setSelectedEdge] = useState<CanvasGraphEdge>();
  const [selectedDetail, setSelectedDetail] = useState<GraphElementDetail>();
  const [detailStatus, setDetailStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [flowInstance, setFlowInstance] =
    useState<ReactFlowInstance<HeliosFlowNode, HeliosFlowEdge>>();
  const [zoomLevel, setZoomLevel] = useState<"low" | "medium" | "high">(
    "medium",
  );
  const [lens, setLens] = useState<GraphLens>("semantic");
  const [breadcrumbs, setBreadcrumbs] = useState<
    Array<{ id: string | null; label: string }>
  >([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResults, setSearchResults] = useState<HeliosGraphNodeDto[]>([]);
  const [searching, setSearching] = useState(false);
  const model = context.models.find(
    (item) => item.id === context.selectedModelId,
  );

  useEffect(() => {
    let active = true;
    setDto(undefined);
    setSelectedNode(undefined);
    setSelectedEdge(undefined);
    setExpandedDatasetIds(new Set());
    setExpandedNodeIds(new Set());
    setExpansionChildren(new Map());
    setBreadcrumbs(model ? [{ id: null, label: model.name }] : []);
    if (!context.selectedModelId) {
      setStatus("idle");
      return () => {
        active = false;
      };
    }

    setStatus("loading");
    setErrorMessage("");
    void context
      .loadModelGraph(context.selectedModelId, {
        navigation: true,
        lens,
        depth: 1,
        limit: 120,
      })
      .then((result) => {
        if (!active) return;
        setDto(result);
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setErrorMessage(describeGraphError(error));
        setStatus("error");
      });

    return () => {
      active = false;
    };
  }, [
    context.loadModelGraph,
    context.selectedModelId,
    model?.name,
    retryVersion,
  ]);

  const graph = useMemo(
    () =>
      dto ? adaptHeliosGraph(dto, { expandedDatasetIds }) : undefined,
    [dto, expandedDatasetIds],
  );
  const flowGraph = useMemo(
    () => (graph ? adaptCanvasGraphToReactFlow(graph) : undefined),
    [graph],
  );

  useEffect(() => {
    if (!context.selectedModelId || searchTerm.trim().length < 2) {
      setSearchResults([]);
      setSearching(false);
      return;
    }
    let active = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      void context
        .loadModelGraph(context.selectedModelId, {
          navigation: true,
          lens,
          query: searchTerm.trim(),
          limit: 20,
        })
        .then((result) => {
          if (!active) return;
          setSearchResults(result.nodes);
          setSearching(false);
        })
        .catch(() => {
          if (!active) return;
          setSearchResults([]);
          setSearching(false);
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [context.loadModelGraph, context.selectedModelId, lens, searchTerm]);

  useEffect(() => {
    const elementId = selectedNode?.id ?? selectedEdge?.id;
    if (!elementId || !context.selectedModelId) {
      setSelectedDetail(undefined);
      setDetailStatus("idle");
      return;
    }
    let active = true;
    setSelectedDetail(undefined);
    setDetailStatus("loading");
    void context
      .loadGraphElementDetail(context.selectedModelId, elementId)
      .then((detail) => {
        if (!active) return;
        setSelectedDetail(detail);
        setDetailStatus("ready");
      })
      .catch(() => {
        if (!active) return;
        setDetailStatus("error");
      });
    return () => {
      active = false;
    };
  }, [
    context.loadGraphElementDetail,
    context.selectedModelId,
    selectedEdge?.id,
    selectedNode?.id,
  ]);

  const onNodeClick: NodeMouseHandler<HeliosFlowNode> = (_, node) => {
    setSelectedNode(node.data.graphNode);
    setSelectedEdge(undefined);
  };
  const onEdgeClick: EdgeMouseHandler<HeliosFlowEdge> = (_, edge) => {
    setSelectedEdge(edge.data?.graphEdge);
    setSelectedNode(undefined);
  };

  async function expandNeighbors(node: CanvasGraphNode) {
    if (!context.selectedModelId || !dto) return;
    const result = await context.loadModelGraph(context.selectedModelId, {
      navigation: true,
      lens,
      focusNodeId: node.id,
      depth: 1,
      includeAttributes: node.category === "dataset",
      limit: 120,
    });
    const existingIds = new Set(dto.nodes.map((item) => item.id));
    const addedIds = new Set(
      result.nodes
        .map((item) => item.id)
        .filter((nodeId) => !existingIds.has(nodeId)),
    );
    setDto((current) => current && mergeGraphDtos(current, result));
    setExpansionChildren((current) => {
      const next = new Map(current);
      next.set(node.id, addedIds);
      return next;
    });
    setExpandedNodeIds((current) => new Set(current).add(node.id));
    if (node.category === "dataset") {
      setExpandedDatasetIds((current) => new Set(current).add(node.id));
    }
    fitNodeIds([node.id, ...addedIds]);
  }

  function collapseBranch(nodeId: string) {
    const remove = new Set<string>();
    const visit = (parentId: string) => {
      for (const childId of expansionChildren.get(parentId) ?? []) {
        if (remove.has(childId)) continue;
        remove.add(childId);
        visit(childId);
      }
    };
    visit(nodeId);
    setDto((current) => current && removeGraphNodes(current, remove));
    setExpandedNodeIds((current) => {
      const next = new Set(current);
      next.delete(nodeId);
      for (const removed of remove) next.delete(removed);
      return next;
    });
    setExpandedDatasetIds((current) => {
      const next = new Set(current);
      next.delete(nodeId);
      for (const removed of remove) next.delete(removed);
      return next;
    });
    setExpansionChildren((current) => {
      const next = new Map(current);
      next.delete(nodeId);
      for (const removed of remove) next.delete(removed);
      return next;
    });
  }

  async function showConnected(node: CanvasGraphNode) {
    if (!context.selectedModelId) return;
    const result = await context.loadModelGraph(context.selectedModelId, {
      navigation: true,
      lens,
      focusNodeId: node.id,
      depth: 1,
      includeAttributes: node.category === "dataset",
      limit: 120,
    });
    setDto(result);
    setExpandedNodeIds(new Set([node.id]));
    setExpandedDatasetIds(
      node.category === "dataset" ? new Set([node.id]) : new Set(),
    );
    setExpansionChildren(new Map());
    setBreadcrumbs((current) => [
      ...current,
      { id: node.id, label: node.label },
    ]);
    fitNodeIds(result.nodes.map((item) => item.id));
  }

  async function restoreBreadcrumb(
    crumb: { id: string | null; label: string },
    index: number,
  ) {
    if (!context.selectedModelId) return;
    const result = await context.loadModelGraph(context.selectedModelId, {
      navigation: true,
      lens,
      focusNodeId: crumb.id ?? undefined,
      depth: 1,
      limit: 120,
    });
    setDto(result);
    setBreadcrumbs((current) => current.slice(0, index + 1));
    setExpandedNodeIds(new Set());
    setExpandedDatasetIds(new Set());
    setExpansionChildren(new Map());
    fitNodeIds(result.nodes.map((item) => item.id));
  }

  async function switchLens(nextLens: GraphLens) {
    if (!context.selectedModelId || nextLens === lens) return;
    const currentFocus = breadcrumbs.at(-1)?.id ?? undefined;
    try {
      let result: HeliosGraphDto;
      let focusPreserved = true;
      try {
        result = await context.loadModelGraph(context.selectedModelId, {
          navigation: true,
          lens: nextLens,
          focusNodeId: currentFocus,
          depth: 1,
          limit: 120,
        });
      } catch {
        focusPreserved = false;
        result = await context.loadModelGraph(context.selectedModelId, {
          navigation: true,
          lens: nextLens,
          depth: 1,
          limit: 120,
        });
      }
      setLens(nextLens);
      setDto(result);
      setExpandedNodeIds(new Set());
      setExpandedDatasetIds(new Set());
      setExpansionChildren(new Map());
      if (!focusPreserved) {
        setSelectedNode(undefined);
        setSelectedEdge(undefined);
        setBreadcrumbs(model ? [{ id: null, label: model.name }] : []);
      }
      fitNodeIds(result.nodes.map((item) => item.id));
    } catch (error) {
      setErrorMessage(describeGraphError(error));
      setStatus("error");
    }
  }

  function focusNode(nodeId: string) {
    const flowNode = flowInstance?.getNode(nodeId);
    if (!flowNode) return;
    void flowInstance?.setCenter(
      flowNode.position.x + (flowNode.measured?.width ?? 210) / 2,
      flowNode.position.y + (flowNode.measured?.height ?? 76) / 2,
      { zoom: 1.2, duration: 350 },
    );
  }

  function fitNodeIds(nodeIds: Iterable<string>) {
    window.requestAnimationFrame(() => {
      const ids = new Set(nodeIds);
      const nodes = flowInstance
        ?.getNodes()
        .filter((node) => ids.has(node.id));
      if (nodes?.length) {
        void flowInstance?.fitView({
          nodes,
          padding: 0.24,
          duration: 350,
        });
      }
    });
  }

  function handleMoveEnd(_: MouseEvent | TouchEvent | null, viewport: Viewport) {
    setZoomLevel(
      viewport.zoom < 0.45
        ? "low"
        : viewport.zoom > 0.9
          ? "high"
          : "medium",
    );
  }

  return (
    <div className="canvas-page">
      <header className="page-header canvas-page-header">
        <h1>{model?.name ?? "Canvas"}</h1>
        {graph ? (
          <span className="canvas-count">
            {graph.nodes.length} nodes · {graph.edges.length} edges
          </span>
        ) : null}
      </header>
      {model ? (
        <div className="canvas-navigation">
          <div className="canvas-lenses" role="group" aria-label="Canvas lens">
            {(["physical", "semantic", "ontology"] as const).map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={lens === item}
                onClick={() => void switchLens(item)}
              >
                {item.charAt(0).toUpperCase() + item.slice(1)}
              </button>
            ))}
          </div>
          <nav className="canvas-breadcrumbs" aria-label="Canvas focus">
            {breadcrumbs.map((crumb, index) => (
              <span key={`${crumb.id ?? "root"}:${index}`}>
                {index > 0 ? <span aria-hidden="true">/</span> : null}
                <button
                  type="button"
                  aria-current={
                    index === breadcrumbs.length - 1 ? "location" : undefined
                  }
                  onClick={() => void restoreBreadcrumb(crumb, index)}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </nav>
          <div className="canvas-search">
            <label>
              <span className="sr-only">Search model graph</span>
              <input
                type="search"
                value={searchTerm}
                placeholder="Search nodes"
                onChange={(event) => setSearchTerm(event.target.value)}
              />
            </label>
            {searchTerm.trim().length >= 2 ? (
              <div className="canvas-search__results">
                {searching ? (
                  <span>Searching…</span>
                ) : searchResults.length ? (
                  searchResults.map((result) => (
                    <button
                      key={result.id}
                      type="button"
                      onClick={() => {
                        setSearchTerm("");
                        setSearchResults([]);
                        void showConnected(searchNode(result));
                      }}
                    >
                      <strong>{result.label}</strong>
                      <span>{result.kind}</span>
                    </button>
                  ))
                ) : (
                  <span>No matching nodes</span>
                )}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {!model ? (
        <EmptyState
          title="Select a model"
          message="Choose an accessible model before opening the canvas."
        />
      ) : status === "loading" || status === "idle" ? (
        <CanvasLoadingState />
      ) : status === "error" ? (
        <ErrorState
          title="Canvas could not be loaded"
          message={errorMessage}
          onRetry={() => {
            context.retry();
            setRetryVersion((version) => version + 1);
          }}
        />
      ) : !graph || !flowGraph || graph.nodes.length === 0 ? (
        <EmptyState
          title="Graph is empty"
          message="The Helios API returned no visible graph nodes for this model."
        />
      ) : (
        <>
          {graph.isLarge ? (
            <div className="canvas-notice canvas-notice--warning" role="status">
              Large graph: the API returned {graph.sourceNodeCount} nodes and{" "}
              {graph.sourceEdgeCount} edges. Attributes remain collapsed to
              keep navigation responsive.
            </div>
          ) : graph.hiddenAttributeCount > 0 ? (
            <div className="canvas-notice" role="status">
              {graph.hiddenAttributeCount} attributes are hidden. Select a
              dataset to reveal its attributes.
            </div>
          ) : null}

          <div className="canvas-workspace">
            <section
              className={`canvas-surface canvas-surface--zoom-${zoomLevel}`}
              aria-label="Model graph"
            >
              <ReactFlow<HeliosFlowNode, HeliosFlowEdge>
                nodes={flowGraph.nodes}
                edges={flowGraph.edges}
                nodeTypes={nodeTypes}
                nodesDraggable={false}
                nodesConnectable={false}
                onInit={setFlowInstance}
                onNodeClick={onNodeClick}
                onEdgeClick={onEdgeClick}
                onMoveEnd={handleMoveEnd}
                onPaneClick={() => {
                  setSelectedNode(undefined);
                  setSelectedEdge(undefined);
                }}
                fitView
                fitViewOptions={{ padding: 0.18 }}
                minZoom={0.15}
                maxZoom={2}
              >
                <Background gap={20} size={1} />
                <Controls showInteractive={false} />
              </ReactFlow>
            </section>
            <CanvasInspector
              node={selectedNode}
              edge={selectedEdge}
              detail={selectedDetail}
              detailStatus={detailStatus}
              datasetExpanded={
                selectedNode
                  ? expandedNodeIds.has(selectedNode.id)
                  : false
              }
              onExpand={(node) => void expandNeighbors(node)}
              onCollapse={collapseBranch}
              onFocus={focusNode}
              onShowConnected={(node) => void showConnected(node)}
              onFitSubgraph={(nodeId) => {
                const connected = new Set([nodeId]);
                for (const edge of graph.edges) {
                  if (edge.source === nodeId) connected.add(edge.target);
                  if (edge.target === nodeId) connected.add(edge.source);
                }
                fitNodeIds(connected);
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}

function CanvasLoadingState() {
  return (
    <div className="canvas-loading" role="status">
      <span className="spinner" aria-hidden="true" />
      <div>
        <strong>Loading model graph</strong>
        <span>Preparing the authorized canvas…</span>
      </div>
    </div>
  );
}

function CanvasInspector({
  node,
  edge,
  detail,
  detailStatus,
  datasetExpanded: expanded,
  onExpand,
  onCollapse,
  onFocus,
  onShowConnected,
  onFitSubgraph,
}: {
  node?: CanvasGraphNode;
  edge?: CanvasGraphEdge;
  detail?: GraphElementDetail;
  detailStatus: "idle" | "loading" | "ready" | "error";
  datasetExpanded: boolean;
  onExpand: (node: CanvasGraphNode) => void;
  onCollapse: (nodeId: string) => void;
  onFocus: (nodeId: string) => void;
  onShowConnected: (node: CanvasGraphNode) => void;
  onFitSubgraph: (nodeId: string) => void;
}) {
  const item = node ?? edge;
  if (!item) return null;

  return (
    <aside className="canvas-inspector" aria-label="Graph selection">
      <p className="section-eyebrow">
        {node ? `${node.kind} node` : "Relationship"}
      </p>
      <h2>{node?.label ?? edge?.kind}</h2>
      <dl className="canvas-inspector__details">
        <InspectorDetail label="Type" value={item.kind} />
        <InspectorDetail
          label="Governance status"
          value={detail?.status ?? item.status}
        />
        {(detail?.confidence ?? item.confidence) !== null ? (
          <InspectorDetail
            label="Confidence"
            value={`${Math.round(
              (detail?.confidence ?? item.confidence ?? 0) * 100,
            )}%`}
          />
        ) : null}
        {edge ? (
          <>
            <InspectorDetail
              label="Source"
              value={detail?.source ?? edge.source}
            />
            <InspectorDetail
              label="Target"
              value={detail?.target ?? edge.target}
            />
          </>
        ) : null}
        {detail?.evidence || item.evidence ? (
          <InspectorDetail
            label="Evidence"
            value={detail?.evidence ?? item.evidence ?? ""}
          />
        ) : null}
      </dl>

      {detailStatus === "loading" ? (
        <div className="canvas-inspector__loading" role="status">
          Loading details…
        </div>
      ) : detailStatus === "error" ? (
        <p className="canvas-inspector__error">
          Additional details are unavailable.
        </p>
      ) : detail && Object.keys(detail.details).length ? (
        <div className="canvas-inspector__metadata">
          <h3>{inspectorSectionTitle(detail.kind, detail.element_type)}</h3>
          <dl>
            {Object.entries(detail.details).map(([key, value]) => (
              <InspectorDetail
                key={key}
                label={detailLabel(key)}
                value={formatDetailValue(key, value)}
              />
            ))}
          </dl>
        </div>
      ) : null}

      {node ? (
        <div className="canvas-inspector__actions">
          <button
            className="button button--secondary"
            type="button"
            onClick={() => onFocus(node.id)}
          >
            Focus on node
          </button>
          <button
            className="button button--secondary"
            type="button"
            onClick={() => onShowConnected(node)}
          >
            Show connected
          </button>
          <button
            className="button button--secondary"
            type="button"
            onClick={() => onFitSubgraph(node.id)}
          >
            Fit selected subgraph
          </button>
          {expanded ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => onCollapse(node.id)}
            >
              Collapse branch
            </button>
          ) : (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => onExpand(node)}
            >
              {node.category === "dataset"
                ? "Expand attributes"
                : "Expand neighbors"}
            </button>
          )}
        </div>
      ) : null}
    </aside>
  );
}

function InspectorDetail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label.replaceAll("_", " ")}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function inspectorSectionTitle(kind: string, elementType: "node" | "edge") {
  if (elementType === "edge") return "Relationship details";
  if (kind === "dataset") return "Dataset details";
  if (kind === "attribute" || kind === "column") return "Attribute details";
  return "Details";
}

function detailLabel(key: string): string {
  const labels: Record<string, string> = {
    physical_identity: "Physical identity",
    data_source_ids: "Data source",
    row_count: "Rows",
    semantic_role: "Semantic role",
    relationship_count: "Relationships",
    physical_type: "Physical type",
    null_percentage: "Null percentage",
    distinct_values: "Distinct values",
    business_terms: "Business term",
    source_physical_identity: "Physical source",
    target_physical_identity: "Physical target",
    source_columns: "Source columns",
    target_columns: "Target columns",
    match_ratio: "Match ratio",
  };
  return labels[key] ?? key.replaceAll("_", " ");
}

function formatDetailValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (key === "null_percentage" && typeof value === "number") {
    return `${value.toFixed(2)}%`;
  }
  if (key === "match_ratio" && typeof value === "number") {
    return `${(value * 100).toFixed(2)}%`;
  }
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString();
  return JSON.stringify(value);
}

function mergeGraphDtos(
  current: HeliosGraphDto,
  incoming: HeliosGraphDto,
): HeliosGraphDto {
  const nodes = new Map(current.nodes.map((node) => [node.id, node]));
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]));
  for (const node of incoming.nodes) nodes.set(node.id, node);
  for (const edge of incoming.edges) edges.set(edge.id, edge);
  const navigation = incoming.navigation ?? current.navigation;
  return rebuildGraphDto(
    current,
    [...nodes.values()],
    [...edges.values()],
    navigation
      ? {
          ...navigation,
          expandable_node_ids: [
            ...new Set([
              ...(current.navigation?.expandable_node_ids ?? []),
              ...(incoming.navigation?.expandable_node_ids ?? []),
            ]),
          ],
          hidden_neighbor_count: {
            ...(current.navigation?.hidden_neighbor_count ?? {}),
            ...(incoming.navigation?.hidden_neighbor_count ?? {}),
          },
        }
      : undefined,
  );
}

function removeGraphNodes(
  current: HeliosGraphDto,
  remove: ReadonlySet<string>,
): HeliosGraphDto {
  return rebuildGraphDto(
    current,
    current.nodes.filter((node) => !remove.has(node.id)),
    current.edges.filter(
      (edge) => !remove.has(edge.source) && !remove.has(edge.target),
    ),
    current.navigation,
  );
}

function rebuildGraphDto(
  source: HeliosGraphDto,
  nodes: HeliosGraphDto["nodes"],
  edges: HeliosGraphDto["edges"],
  navigation: HeliosGraphDto["navigation"],
): HeliosGraphDto {
  return {
    ...source,
    nodes,
    edges,
    summary: {
      node_count: nodes.length,
      edge_count: edges.length,
      node_kinds: [...new Set(nodes.map((node) => node.kind))].sort(),
      edge_kinds: [...new Set(edges.map((edge) => edge.kind))].sort(),
    },
    navigation,
  };
}

function searchNode(node: HeliosGraphNodeDto): CanvasGraphNode {
  return {
    id: node.id,
    kind: node.kind,
    category: ["domain", "concept", "dataset", "attribute", "metric"].includes(
      node.kind,
    )
      ? (node.kind as CanvasGraphNode["category"])
      : "generic",
    label: node.label,
    status: node.status,
    confidence: node.confidence,
    evidence: node.evidence,
    metadata: node.metadata,
    permittedActions: node.permitted_actions,
  };
}

function describeGraphError(error: unknown): string {
  if (error instanceof AuthenticationError) {
    return "Authentication with Helios failed.";
  }
  if (error instanceof AuthorizationError) {
    return "You no longer have permission to view this model graph.";
  }
  if (error instanceof ApiUnavailableError) return error.message;
  return "The Helios graph API could not be reached.";
}
