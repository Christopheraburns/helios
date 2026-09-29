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
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ApiUnavailableError,
  AuthenticationError,
  AuthorizationError,
  GraphElementDetail,
  GraphLens,
  HeliosGraphEdgeDto,
  HeliosGraphDto,
  HeliosGraphNodeDto,
  ReviewSection,
  ReviewSummary,
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
  const [searchParams, setSearchParams] = useSearchParams();
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
  const [reviewMode, setReviewMode] = useState(false);
  const [reviewRunId, setReviewRunId] = useState<string>();
  const [mutationStatus, setMutationStatus] = useState<
    "idle" | "saving" | "success" | "error"
  >("idle");
  const [mutationError, setMutationError] = useState("");
  const [reviewSummary, setReviewSummary] = useState<ReviewSummary>();
  const [reviewStatus, setReviewStatus] = useState<CanvasStatus>("idle");
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.85);
  const [breadcrumbs, setBreadcrumbs] = useState<
    Array<{ id: string | null; label: string }>
  >([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchResults, setSearchResults] = useState<HeliosGraphNodeDto[]>([]);
  const [searching, setSearching] = useState(false);
  const [pendingFitNodeIds, setPendingFitNodeIds] = useState<string[]>([]);
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  const suppressedSearchRef = useRef<
    { modelId: string; signature: string } | undefined
  >(undefined);
  const model = context.models.find(
    (item) => item.id === context.selectedModelId,
  );
  const latestReviewRunId =
    context.modelOverview?.lifecycle.latest_run_id ?? undefined;
  const canReview =
    Boolean(latestReviewRunId) &&
    Boolean(
      context.modelOverview?.available_actions.includes("model.edit"),
    );
  const requestedReviewRunId = searchParams.get("review_run_id") ?? "";
  const requestedLens = parseLens(searchParams.get("lens"));
  const requestedFocusNodeId = searchParams.get("focus_node_id") ?? "";
  const requestedElementId = searchParams.get("element_id") ?? "";
  const requestedRelatedNodeIds = parseRelatedNodeIds(
    searchParams.get("related_node_ids"),
  );
  const canvasSearchSignature = canvasSearchKey(searchParams);

  function updateCanvasSearch(
    updates: Record<string, string | undefined>,
    replace = false,
  ) {
    const next = new URLSearchParams(searchParamsRef.current);
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    suppressedSearchRef.current = {
      modelId: context.selectedModelId,
      signature: canvasSearchKey(next),
    };
    searchParamsRef.current = next;
    setSearchParams(next, { replace });
  }

  useEffect(() => {
    if (
      suppressedSearchRef.current?.modelId === context.selectedModelId &&
      suppressedSearchRef.current.signature === canvasSearchSignature
    ) {
      suppressedSearchRef.current = undefined;
      return;
    }
    let active = true;
    const initialLens = requestedLens ?? "semantic";
    const relatedNodeIds = requestedRelatedNodeIds;
    setDto(undefined);
    setSelectedNode(undefined);
    setSelectedEdge(undefined);
    setSelectedDetail(undefined);
    setDetailStatus("idle");
    setExpandedDatasetIds(new Set());
    setExpandedNodeIds(new Set());
    setExpansionChildren(new Map());
    setBreadcrumbs(model ? [{ id: null, label: model.name }] : []);
    setReviewMode(false);
    setReviewRunId(undefined);
    setReviewSummary(undefined);
    setReviewStatus("idle");
    setMutationStatus("idle");
    setMutationError("");
    setLens(initialLens);
    setSearchTerm("");
    setSearchResults([]);
    setSearching(false);
    setZoomLevel("medium");
    setPendingFitNodeIds([]);
    if (!context.selectedModelId) {
      setStatus("idle");
      return () => {
        active = false;
      };
    }

    setStatus("loading");
    setErrorMessage("");
    async function loadInitialGraph() {
      const modelId = context.selectedModelId;
      const urlUpdates: Record<string, string | undefined> = {};
      let activeReviewRunId = requestedReviewRunId || undefined;
      let summary: ReviewSummary | undefined;
      let focusNodeId = requestedFocusNodeId || undefined;
      let elementId = requestedElementId || undefined;
      let effectiveRelatedIds = relatedNodeIds;
      let result: HeliosGraphDto;

      if (searchParams.get("lens") && !requestedLens) {
        urlUpdates.lens = undefined;
      }
      if (activeReviewRunId) {
        try {
          summary = await context.loadModelReview(modelId, activeReviewRunId);
        } catch {
          activeReviewRunId = undefined;
          focusNodeId = undefined;
          elementId = undefined;
          effectiveRelatedIds = [];
          urlUpdates.review_run_id = undefined;
          urlUpdates.focus_node_id = undefined;
          urlUpdates.element_id = undefined;
          urlUpdates.related_node_ids = undefined;
        }
      }

      const graphOptions = {
        navigation: true as const,
        lens: initialLens,
        ...(activeReviewRunId ? { reviewRunId: activeReviewRunId } : {}),
        depth: 1,
        limit: 120,
      };
      try {
        result = await context.loadModelGraph(modelId, {
          ...graphOptions,
          ...(focusNodeId ? { focusNodeId } : {}),
        });
      } catch (error) {
        if (!focusNodeId) throw error;
        result = await context.loadModelGraph(modelId, graphOptions);
        focusNodeId = undefined;
        elementId = undefined;
        effectiveRelatedIds = [];
        urlUpdates.focus_node_id = undefined;
        urlUpdates.element_id = undefined;
        urlUpdates.related_node_ids = undefined;
      }

      if (!active) return;
      const nodeIds = new Set(result.nodes.map((node) => node.id));
      const edge = elementId
        ? result.edges.find((item) => item.id === elementId)
        : undefined;
      const node = elementId
        ? result.nodes.find((item) => item.id === elementId)
        : undefined;
      if (elementId && !node && !edge) {
        elementId = undefined;
        effectiveRelatedIds = [];
        urlUpdates.element_id = undefined;
        urlUpdates.related_node_ids = undefined;
      }
      const validRelatedIds = effectiveRelatedIds.filter((id) =>
        nodeIds.has(id),
      );
      if (validRelatedIds.length !== effectiveRelatedIds.length) {
        effectiveRelatedIds = validRelatedIds;
        urlUpdates.related_node_ids = validRelatedIds.length
          ? validRelatedIds.join(",")
          : undefined;
      }

      const fitIds = new Set(validRelatedIds);
      if (focusNodeId && nodeIds.has(focusNodeId)) fitIds.add(focusNodeId);
      if (node) fitIds.add(node.id);
      if (edge) {
        fitIds.add(edge.source);
        fitIds.add(edge.target);
      }
      const expandedDatasets = datasetsToExpand(result, fitIds);
      setExpandedDatasetIds(expandedDatasets);
      setDto(result);
      setReviewRunId(activeReviewRunId);
      setReviewMode(Boolean(activeReviewRunId));
      setReviewSummary(summary);
      setReviewStatus(summary ? "ready" : "idle");
      if (node) setSelectedNode(searchNode(node));
      if (edge) setSelectedEdge(searchEdge(edge));
      if (focusNodeId) {
        const focusNode = result.nodes.find((item) => item.id === focusNodeId);
        if (focusNode) {
          setBreadcrumbs([
            ...(model ? [{ id: null, label: model.name }] : []),
            { id: focusNode.id, label: focusNode.label },
          ]);
        }
      }
      setPendingFitNodeIds(
        fitIds.size ? [...fitIds] : result.nodes.map((item) => item.id),
      );
      setStatus("ready");
      if (Object.keys(urlUpdates).length) {
        updateCanvasSearch(urlUpdates, true);
      }
    }

    void loadInitialGraph().catch((error: unknown) => {
        if (!active) return;
        setErrorMessage(describeGraphError(error));
        setStatus("error");
      });

    return () => {
      active = false;
    };
  }, [
    context.loadModelGraph,
    context.loadModelReview,
    context.selectedModelId,
    canvasSearchSignature,
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
    if (!flowInstance || !flowGraph || !pendingFitNodeIds.length) return;
    fitNodeIds(pendingFitNodeIds);
    setPendingFitNodeIds([]);
  }, [flowGraph, flowInstance, pendingFitNodeIds]);

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
          ...(reviewMode && reviewRunId ? { reviewRunId } : {}),
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
  }, [
    context.loadModelGraph,
    context.selectedModelId,
    lens,
    reviewMode,
    reviewRunId,
    searchTerm,
  ]);

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
      .loadGraphElementDetail(
        context.selectedModelId,
        elementId,
        reviewMode ? reviewRunId : undefined,
      )
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
    reviewMode,
    reviewRunId,
    selectedEdge?.id,
    selectedNode?.id,
  ]);

  const onNodeClick: NodeMouseHandler<HeliosFlowNode> = (_, node) => {
    setSelectedNode(node.data.graphNode);
    setSelectedEdge(undefined);
    updateCanvasSearch({
      element_id: node.id,
      focus_node_id: node.id,
      related_node_ids: undefined,
    });
  };
  const onEdgeClick: EdgeMouseHandler<HeliosFlowEdge> = (_, edge) => {
    setSelectedEdge(edge.data?.graphEdge);
    setSelectedNode(undefined);
    updateCanvasSearch({
      element_id: edge.id,
      focus_node_id: edge.source,
      related_node_ids: [edge.source, edge.target].join(","),
    });
  };

  async function expandNeighbors(node: CanvasGraphNode) {
    if (!context.selectedModelId || !dto) return;
    const result = await context.loadModelGraph(context.selectedModelId, {
      navigation: true,
      lens,
      ...(reviewMode && reviewRunId ? { reviewRunId } : {}),
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
      ...(reviewMode && reviewRunId ? { reviewRunId } : {}),
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
    updateCanvasSearch({
      focus_node_id: node.id,
      element_id: node.id,
      related_node_ids: undefined,
    });
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
      ...(reviewMode && reviewRunId ? { reviewRunId } : {}),
      focusNodeId: crumb.id ?? undefined,
      depth: 1,
      limit: 120,
    });
    setDto(result);
    setBreadcrumbs((current) => current.slice(0, index + 1));
    setExpandedNodeIds(new Set());
    setExpandedDatasetIds(new Set());
    setExpansionChildren(new Map());
    updateCanvasSearch({
      focus_node_id: crumb.id ?? undefined,
      element_id: undefined,
      related_node_ids: undefined,
    });
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
          ...(reviewMode && reviewRunId ? { reviewRunId } : {}),
          focusNodeId: currentFocus,
          depth: 1,
          limit: 120,
        });
      } catch {
        focusPreserved = false;
        result = await context.loadModelGraph(context.selectedModelId, {
          navigation: true,
          lens: nextLens,
          ...(reviewMode && reviewRunId ? { reviewRunId } : {}),
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
      updateCanvasSearch({
        lens: nextLens,
        focus_node_id: focusPreserved ? currentFocus : undefined,
        ...(!focusPreserved
          ? { element_id: undefined, related_node_ids: undefined }
          : {}),
      });
      fitNodeIds(result.nodes.map((item) => item.id));
    } catch (error) {
      setErrorMessage(describeGraphError(error));
      setStatus("error");
    }
  }

  async function toggleReviewMode(
    enabled = !reviewMode,
    updateUrl = true,
  ) {
    const nextReviewRunId = enabled ? latestReviewRunId : undefined;
    if (!context.selectedModelId || (enabled && !nextReviewRunId)) return;
    const nextReviewMode = enabled;
    const currentFocus = breadcrumbs.at(-1)?.id ?? undefined;
    try {
      setReviewStatus(enabled ? "loading" : "idle");
      let result: HeliosGraphDto;
      let summary: ReviewSummary | undefined;
      try {
        if (nextReviewRunId) {
          [result, summary] = await Promise.all([
            context.loadModelGraph(context.selectedModelId, {
              navigation: true,
              lens,
              reviewRunId: nextReviewRunId,
              focusNodeId: currentFocus,
              depth: 1,
              limit: 120,
            }),
            context.loadModelReview(
              context.selectedModelId,
              nextReviewRunId,
            ),
          ]);
        } else {
          result = await context.loadModelGraph(context.selectedModelId, {
            navigation: true,
            lens,
            focusNodeId: currentFocus,
            depth: 1,
            limit: 120,
          });
        }
      } catch {
        result = await context.loadModelGraph(context.selectedModelId, {
          navigation: true,
          lens,
          reviewRunId: nextReviewRunId,
          depth: 1,
          limit: 120,
        });
        summary = nextReviewRunId
          ? await context.loadModelReview(
              context.selectedModelId,
              nextReviewRunId,
            )
          : undefined;
        setBreadcrumbs(model ? [{ id: null, label: model.name }] : []);
        setSelectedNode(undefined);
        setSelectedEdge(undefined);
      }
      setReviewMode(nextReviewMode);
      setReviewRunId(nextReviewRunId);
      setReviewSummary(summary);
      setReviewStatus(summary ? "ready" : "idle");
      setDto(result);
      setExpandedNodeIds(new Set());
      setExpandedDatasetIds(new Set());
      setExpansionChildren(new Map());
      if (updateUrl) {
        updateCanvasSearch({
          review_run_id: nextReviewRunId,
          ...(!nextReviewMode
            ? {
                focus_node_id: undefined,
                element_id: undefined,
                related_node_ids: undefined,
              }
            : {}),
        });
      }
      fitNodeIds(result.nodes.map((item) => item.id));
    } catch (error) {
      setErrorMessage(describeGraphError(error));
      setStatus("error");
    }
  }

  async function decideSelectedProposal(
    decision: "accept" | "reject" | "edit",
    overrides?: Record<string, unknown>,
    note = "",
  ) {
    if (
      !context.selectedModelId ||
      !reviewRunId ||
      !selectedDetail
    ) {
      return;
    }
    const section = selectedDetail.details.review_section;
    const elementId = selectedDetail.details.review_element_id;
    if (typeof section !== "string" || typeof elementId !== "string") return;

    setMutationStatus("saving");
    setMutationError("");
    try {
      const response = await context.decideModelProposal(
        context.selectedModelId,
        reviewRunId,
        {
          section: section as ReviewSection,
          element_id: elementId,
          decision,
          overrides,
          note,
        },
      );
      setReviewSummary(response.summary);
      await reconcileReviewSurface();
      setMutationStatus("idle");
    } catch (error) {
      setMutationStatus("error");
      setMutationError(describeGraphError(error));
    }
  }

  async function reconcileReviewSurface() {
    if (!context.selectedModelId || !reviewRunId) return;
    const focusNodeId = selectedNode?.id;
    const detailId = selectedDetail?.id;
    const refreshed = await context.loadModelGraph(
      context.selectedModelId,
      {
        navigation: true,
        lens,
        reviewRunId,
        focusNodeId,
        depth: 1,
        includeAttributes: selectedNode?.category === "dataset",
        limit: 120,
      },
    );
    setDto(refreshed);
    if (detailId) {
      const detail = await context.loadGraphElementDetail(
        context.selectedModelId,
        detailId,
        reviewRunId,
      );
      setSelectedDetail(detail);
    }
    context.refreshModelOverview();
    fitNodeIds(refreshed.nodes.map((item) => item.id));
  }

  async function cascadeSelectedDataset(
    decision: "accept" | "reject",
  ) {
    if (
      !context.selectedModelId ||
      !reviewRunId ||
      !selectedDetail ||
      selectedDetail.details.review_section !== "datasets"
    ) {
      return;
    }
    const table = selectedDetail.details.review_element_id;
    if (typeof table !== "string") return;
    setMutationStatus("saving");
    setMutationError("");
    try {
      const response = await context.decideModelDataset(
        context.selectedModelId,
        reviewRunId,
        table,
        decision,
      );
      setReviewSummary(response.summary);
      await reconcileReviewSurface();
      setMutationStatus("idle");
    } catch (error) {
      setMutationStatus("error");
      setMutationError(describeGraphError(error));
    }
  }

  async function bulkAccept() {
    if (!context.selectedModelId || !reviewRunId) return;
    setMutationStatus("saving");
    setMutationError("");
    try {
      const response = await context.bulkAcceptModelProposals(
        context.selectedModelId,
        reviewRunId,
        confidenceThreshold,
      );
      setReviewSummary(response.summary);
      await reconcileReviewSurface();
      setMutationStatus("idle");
    } catch (error) {
      setMutationStatus("error");
      setMutationError(describeGraphError(error));
    }
  }

  async function resetReview(section?: ReviewSection) {
    if (
      !context.selectedModelId ||
      !reviewRunId ||
      !window.confirm(
        section
          ? `Reset all ${section.replaceAll("_", " ")} decisions?`
          : "Reset all review decisions?",
      )
    ) {
      return;
    }
    setMutationStatus("saving");
    setMutationError("");
    try {
      const response = await context.resetModelReview(
        context.selectedModelId,
        reviewRunId,
        section,
      );
      setReviewSummary(response.summary);
      await reconcileReviewSurface();
      setMutationStatus("idle");
    } catch (error) {
      setMutationStatus("error");
      setMutationError(describeGraphError(error));
    }
  }

  async function publishReview() {
    if (!context.selectedModelId || !reviewRunId) return;
    setMutationStatus("saving");
    setMutationError("");
    try {
      await context.publishModelReview(context.selectedModelId, reviewRunId);
      const summary = await context.loadModelReview(
        context.selectedModelId,
        reviewRunId,
      );
      setReviewSummary(summary);
      await reconcileReviewSurface();
      setMutationStatus("success");
    } catch (error) {
      setMutationStatus("error");
      setMutationError(describeGraphError(error));
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
          {reviewMode || canReview ? (
            <button
              className="canvas-review-toggle"
              type="button"
              aria-pressed={reviewMode}
              onClick={() => void toggleReviewMode()}
            >
              {reviewMode ? "Reviewing proposals" : "Review proposals"}
            </button>
          ) : null}
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

      {reviewMode ? (
        <section className="review-toolbar" aria-label="Proposal review">
          <div className="review-toolbar__summary">
            <div>
              <p className="section-eyebrow">Review run</p>
              <strong>{reviewRunId}</strong>
            </div>
            {reviewStatus === "loading" ? (
              <span role="status">Loading review summary…</span>
            ) : reviewSummary ? (
              <ul aria-label="Review decision counts">
                {(
                  Object.entries(reviewSummary.sections) as Array<
                    [ReviewSection, ReviewSummary["sections"][ReviewSection]]
                  >
                ).map(([section, counts]) => (
                  <li key={section}>
                    <strong>{section.replaceAll("_", " ")}</strong>
                    <span>
                      {counts.pending} pending · {counts.accept} accepted ·{" "}
                      {counts.reject} rejected · {counts.edit} edited
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          {reviewSummary ? (
            <>
              <div className="review-toolbar__audit">
                {reviewSummary.reviewed_at ? (
                  <span>
                    Last reviewed by{" "}
                    {reviewSummary.reviewed_by ?? "an authorized user"} ·{" "}
                    {formatReviewDate(reviewSummary.reviewed_at)}
                  </span>
                ) : (
                  <span>No review decisions recorded yet.</span>
                )}
                {reviewSummary.validation_errors.length ? (
                  <span>
                    {reviewSummary.validation_errors.length} publication{" "}
                    {reviewSummary.validation_errors.length === 1
                      ? "issue"
                      : "issues"}
                  </span>
                ) : (
                  <span>Publication validation passed.</span>
                )}
              </div>
              <div className="review-toolbar__actions">
                {reviewSummary.available_actions.includes("bulk_accept") ? (
                  <label>
                    Minimum confidence
                    <input
                      aria-label="Minimum confidence"
                      max="1"
                      min="0"
                      step="0.05"
                      type="number"
                      value={confidenceThreshold}
                      onChange={(event) =>
                        setConfidenceThreshold(
                          Math.min(
                            1,
                            Math.max(0, Number(event.target.value)),
                          ),
                        )
                      }
                    />
                    <button
                      className="button button--secondary"
                      disabled={mutationStatus === "saving"}
                      onClick={() => void bulkAccept()}
                      type="button"
                    >
                      Accept above threshold
                    </button>
                  </label>
                ) : null}
                {reviewSummary.available_actions.includes("reset") ? (
                  <button
                    className="button button--secondary"
                    disabled={mutationStatus === "saving"}
                    onClick={() => void resetReview()}
                    type="button"
                  >
                    Reset review
                  </button>
                ) : null}
                {reviewSummary.available_actions.includes("publish") ? (
                  <button
                    className="button button--primary"
                    disabled={
                      mutationStatus === "saving" ||
                      !reviewSummary.publish_ready
                    }
                    onClick={() => void publishReview()}
                    title={
                      reviewSummary.publish_ready
                        ? undefined
                        : "Resolve publication validation issues first."
                    }
                    type="button"
                  >
                    {mutationStatus === "saving"
                      ? "Saving…"
                      : "Publish model"}
                  </button>
                ) : null}
              </div>
              {mutationStatus === "success" ? (
                <p role="status">Model published successfully.</p>
              ) : mutationError ? (
                <p role="alert">{mutationError}</p>
              ) : null}
            </>
          ) : reviewStatus === "error" ? (
            <p role="alert">
              {mutationError || "The review summary could not be loaded."}
            </p>
          ) : null}
        </section>
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
                  updateCanvasSearch({
                    element_id: undefined,
                    related_node_ids: undefined,
                  });
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
              mutationStatus={mutationStatus}
              mutationError={mutationError}
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
              onDecision={decideSelectedProposal}
              onDatasetDecision={cascadeSelectedDataset}
              canCascadeDataset={
                reviewSummary?.available_actions.includes("cascade") ?? false
              }
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
  mutationStatus,
  mutationError,
  datasetExpanded: expanded,
  onExpand,
  onCollapse,
  onFocus,
  onShowConnected,
  onFitSubgraph,
  onDecision,
  onDatasetDecision,
  canCascadeDataset,
}: {
  node?: CanvasGraphNode;
  edge?: CanvasGraphEdge;
  detail?: GraphElementDetail;
  detailStatus: "idle" | "loading" | "ready" | "error";
  mutationStatus: "idle" | "saving" | "success" | "error";
  mutationError: string;
  datasetExpanded: boolean;
  onExpand: (node: CanvasGraphNode) => void;
  onCollapse: (nodeId: string) => void;
  onFocus: (nodeId: string) => void;
  onShowConnected: (node: CanvasGraphNode) => void;
  onFitSubgraph: (nodeId: string) => void;
  onDecision: (
    decision: "accept" | "reject" | "edit",
    overrides?: Record<string, unknown>,
    note?: string,
  ) => void;
  onDatasetDecision: (decision: "accept" | "reject") => void;
  canCascadeDataset: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [editNote, setEditNote] = useState("");
  const [editOverrides, setEditOverrides] = useState("{}");
  const [editError, setEditError] = useState("");
  const item = node ?? edge;
  if (!item) return null;
  const canMutateProposal =
    detail?.available_actions.includes("model.edit") &&
    typeof detail.details.review_section === "string" &&
    typeof detail.details.review_element_id === "string";

  function submitEdit() {
    try {
      const parsed = JSON.parse(editOverrides) as unknown;
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        Array.isArray(parsed)
      ) {
        throw new Error("Overrides must be a JSON object.");
      }
      setEditError("");
      onDecision(
        "edit",
        parsed as Record<string, unknown>,
        editNote,
      );
      setEditing(false);
    } catch (error) {
      setEditError(
        error instanceof Error ? error.message : "Overrides are invalid.",
      );
    }
  }

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

      {canMutateProposal ? (
        <div className="canvas-inspector__review-actions">
          <h3>Review proposal</h3>
          <div>
            <button
              className="button button--primary"
              type="button"
              disabled={mutationStatus === "saving"}
              onClick={() => onDecision("accept")}
            >
              Approve
            </button>
            <button
              className="button button--secondary"
              type="button"
              disabled={mutationStatus === "saving"}
              onClick={() => onDecision("reject")}
            >
              Reject
            </button>
            <button
              className="button button--secondary"
              type="button"
              disabled={mutationStatus === "saving"}
              onClick={() => setEditing((current) => !current)}
            >
              Edit
            </button>
          </div>
          {canCascadeDataset &&
          detail?.details.review_section === "datasets" ? (
            <div>
              <button
                className="button button--secondary"
                disabled={mutationStatus === "saving"}
                onClick={() => onDatasetDecision("accept")}
                type="button"
              >
                Approve dataset and attributes
              </button>
              <button
                className="button button--secondary"
                disabled={mutationStatus === "saving"}
                onClick={() => onDatasetDecision("reject")}
                type="button"
              >
                Reject dataset and attributes
              </button>
            </div>
          ) : null}
          {editing ? (
            <div className="canvas-inspector__edit-form">
              <label>
                Note
                <textarea
                  value={editNote}
                  onChange={(event) => setEditNote(event.target.value)}
                />
              </label>
              <label>
                Overrides (JSON)
                <textarea
                  value={editOverrides}
                  onChange={(event) => setEditOverrides(event.target.value)}
                />
              </label>
              {editError ? <p>{editError}</p> : null}
              <button
                className="button button--primary"
                type="button"
                disabled={mutationStatus === "saving"}
                onClick={submitEdit}
              >
                Save edit
              </button>
            </div>
          ) : null}
          {mutationStatus === "saving" ? (
            <span role="status">Saving decision…</span>
          ) : null}
          {mutationStatus === "error" ? (
            <p className="canvas-inspector__error">{mutationError}</p>
          ) : null}
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

function formatReviewDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
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

function searchEdge(edge: HeliosGraphEdgeDto): CanvasGraphEdge {
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

function relationshipCategory(
  kind: string,
): CanvasGraphEdge["category"] {
  const normalized = kind.toLowerCase();
  if (normalized.includes("physical")) return "physical";
  if (normalized.includes("inferred")) return "inferred";
  if (normalized.includes("semantic")) return "semantic";
  if (normalized.includes("ontology")) return "ontology";
  return "generic";
}

function datasetsToExpand(
  dto: HeliosGraphDto,
  relevantNodeIds: ReadonlySet<string>,
): Set<string> {
  const kinds = new Map(dto.nodes.map((node) => [node.id, node.kind]));
  const result = new Set(
    [...relevantNodeIds].filter((id) => kinds.get(id) === "dataset"),
  );
  for (const edge of dto.edges) {
    if (
      relevantNodeIds.has(edge.source) &&
      kinds.get(edge.source) === "attribute" &&
      kinds.get(edge.target) === "dataset"
    ) {
      result.add(edge.target);
    }
    if (
      relevantNodeIds.has(edge.target) &&
      kinds.get(edge.target) === "attribute" &&
      kinds.get(edge.source) === "dataset"
    ) {
      result.add(edge.source);
    }
  }
  return result;
}

function parseLens(value: string | null): GraphLens | undefined {
  return value === "physical" ||
    value === "semantic" ||
    value === "ontology"
    ? value
    : undefined;
}

function parseRelatedNodeIds(value: string | null): string[] {
  if (!value) return [];
  return [...new Set(value.split(",").map((id) => id.trim()).filter(Boolean))]
    .slice(0, 120);
}

function canvasSearchKey(params: URLSearchParams): string {
  return [
    "review_run_id",
    "lens",
    "focus_node_id",
    "element_id",
    "related_node_ids",
  ]
    .map((key) => `${key}=${params.get(key) ?? ""}`)
    .join("&");
}

function describeGraphError(error: unknown): string {
  if (error instanceof AuthenticationError) {
    return "Authentication with Helios failed.";
  }
  if (error instanceof AuthorizationError) {
    return "You no longer have permission to view this model graph.";
  }
  if (error instanceof ApiUnavailableError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "The Helios graph API could not be reached.";
}
