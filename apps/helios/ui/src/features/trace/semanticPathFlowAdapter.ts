import { Edge, Node } from "@xyflow/react";

import { SemanticTraceEvidence, TraceDetail } from "../../api/client";
import { TraceFlow } from "./traceFlowAdapter";
import { TraceNodeData } from "./TraceNode";

const X_GAP = 300;

export function adaptSemanticPathToFlow(detail: TraceDetail): TraceFlow {
  const evidence = detail.semantic_evidence;
  if (!evidence) return unavailableFlow(detail);

  const nodes: Array<Node<TraceNodeData>> = [];
  const edges: Edge[] = [];
  addNode(nodes, "semantic-question", 0, 0, {
    label: "Your question",
    detail: evidence.question,
    category: "question",
    status: detail.run.status,
    explanation: "This is the question you asked Helios.",
  });
  addNode(nodes, "semantic-assistant", 1, 0, {
    label: "Helios understands the request",
    detail: `${evidence.assistant.provider} · ${evidence.assistant.model}`,
    category: "llm",
    status: detail.run.status,
    explanation:
      "Helios asks an AI model to decide which approved data tools can answer your question.",
  });
  connect(edges, "semantic-question", "semantic-assistant");

  const documents = evidence.documents ?? null;
  const warehouseTools = evidence.tools.filter(
    (tool) => !DOCUMENT_TOOLS.has(tool.name),
  );
  // With documents in play, the warehouse lane is drawn only if it was used.
  const warehouseUsed = !documents
    || warehouseTools.length > 0
    || evidence.semantic_objects.length > 0
    || evidence.datasets.length > 0
    || Boolean(evidence.query);
  let previousIds: string[] = [];
  if (warehouseUsed) {
    const toolDetail = warehouseTools.length
      ? warehouseTools.map((tool) => friendlyToolName(tool.name)).join(", ")
      : "No data tools were recorded";
    addNode(nodes, "semantic-tools", 2, 0, {
      label: "Helios checks approved definitions",
      detail: toolDetail,
      category: "tool",
      status: warehouseTools.some((tool) => tool.status === "error")
        ? "error"
        : "success",
      explanation:
        "These controlled tools let Helios look up business definitions and request data without direct, unrestricted access.",
    });
    connect(edges, "semantic-assistant", "semantic-tools");

    const businessObjects = evidence.semantic_objects.length
      ? evidence.semantic_objects
      : [{
        id: "missing-business-meaning",
        kind: "definition",
        name: "No approved definition recorded",
        description: "The trace does not contain a verified semantic match.",
        ossie_pointer: "",
      }];
    businessObjects.forEach((item, index) => {
      const id = `semantic-object-${index}`;
      addNode(nodes, id, 3, index, {
        label: "Business meaning",
        detail: `${item.name} · ${item.kind}`,
        category: "semantic",
        status: evidence.revision?.verified ? "verified" : "incomplete",
        explanation:
          "This is an approved business definition from the exact semantic model version used for this answer.",
        semanticId: item.id,
      });
      connect(edges, "semantic-tools", id);
    });
    if (evidence.revision) {
      addNode(nodes, "semantic-revision", 3, businessObjects.length, {
        label: "Verified definition version",
        detail: evidence.revision.verified
          ? evidence.revision.id.slice(0, 12)
          : "Integrity check failed",
        category: "semantic",
        status: evidence.revision.verified ? "verified" : "error",
        explanation:
          "This fingerprint locks the answer to the exact approved definitions used at the time.",
      });
      businessObjects.forEach((_item, objectIndex) => {
        connect(edges, "semantic-revision", `semantic-object-${objectIndex}`);
      });
    }

    const dataSets = evidence.datasets.length
      ? evidence.datasets
      : [{
        semantic_dataset: null,
        physical_name: "No physical dataset was queried",
        data_source_id: null,
      }];
    dataSets.forEach((dataset, index) => {
      const id = `semantic-dataset-${index}`;
      addNode(nodes, id, 4, index, {
        label: "Data source",
        detail: dataset.physical_name,
        category: "dataset",
        status: dataset.data_source_id ? "verified" : "not used",
        explanation:
          "This is the database table behind the business definition. It shows where the answer's data came from.",
        semanticId: dataset.data_source_id || undefined,
      });
      businessObjects.forEach((_item, objectIndex) => {
        connect(edges, `semantic-object-${objectIndex}`, id);
      });
    });

    previousIds = dataSets.map((_item, index) => `semantic-dataset-${index}`);
    if (evidence.query) {
      addNode(nodes, "semantic-query", 5, 0, {
        label: "Approved query",
        detail: `${evidence.query.columns.length} columns${
          evidence.query.row_count == null
            ? ""
            : ` · ${evidence.query.row_count} rows`
        }`,
        category: "query",
        status: "success",
        explanation:
          "Helios translated the approved definitions into a database request. Technical users can inspect the SQL on the right.",
      });
      previousIds.forEach((id) => connect(edges, id, "semantic-query"));
      previousIds = ["semantic-query"];
    }
  }

  if (documents) {
    // The document lane sits above the warehouse lane, or in its place.
    const lanes = Math.max(documents.searches.length, documents.groups.length, 1);
    const top = warehouseUsed ? -lanes - 0.35 : 0;
    addLaneLabel(nodes, "lane-documents", top, "Documents: emails, chats and reports", "document");
    if (warehouseUsed) {
      addLaneLabel(nodes, "lane-warehouse", 0, "Warehouse: approved definitions and tables", "warehouse");
    }
    documents.searches.forEach((search, index) => {
      const id = `document-search-${index}`;
      addNode(nodes, id, 2, top + index, {
        label: search.tool === "entity_claims"
          ? "Helios looks up claims in documents"
          : "Helios searches documents",
        detail: `“${search.query}”`,
        category: "document",
        status: search.status === "success"
          ? `${search.result_count} found`
          : search.status,
        explanation: search.tool === "entity_claims"
          ? "Helios looked up what crawled documents claim about this person or thing."
          : "Helios searched crawled documents by meaning, not by exact words, and kept the closest passages.",
      });
      connect(edges, "semantic-assistant", id);
    });
    documents.groups.forEach((group, index) => {
      const id = `document-group-${index}`;
      const bridges = documents.bridges.filter((item) => item.group_id === group.id);
      addNode(nodes, id, 4, top + index, {
        label: documentTypeLabel(group.type, group.count),
        detail: bridges.length
          ? `${bridgeLabel(bridges)} in the query`
          : "Text given to the AI model for the answer",
        category: "document",
        status: "found by similarity",
        explanation:
          "These passages were found because they are close in meaning to the search. They are not verified facts; select this step to read them.",
      });
      group.search_ids.forEach((searchId) => {
        const searchIndex = documents.searches.findIndex((item) => item.id === searchId);
        if (searchIndex >= 0) connect(edges, `document-search-${searchIndex}`, id);
      });
      if (bridges.length && evidence.query) connect(edges, id, "semantic-query");
      previousIds.push(id);
    });
    if (!documents.groups.length) {
      previousIds.push(...documents.searches.map((_item, index) => `document-search-${index}`));
    }
  }

  addNode(nodes, "semantic-answer", 6, 0, {
    label: evidence.error ? "Request ended" : "Answer",
    detail:
      evidence.answer
      || evidence.error?.message
      || "No final answer was produced.",
    category: "result",
    status: evidence.status,
    explanation:
      "This is the response produced from the recorded steps shown to the left.",
  });
  previousIds.forEach((id) => connect(edges, id, "semantic-answer"));
  const nodeIds = new Map<string, string>([
    ["question", "semantic-question"],
    ["assistant", "semantic-assistant"],
    ["query", "semantic-query"],
    ["answer", "semantic-answer"],
    ["revision", "semantic-revision"],
    ...warehouseTools.map((tool) => [tool.id, "semantic-tools"] as const),
    ...(documents?.searches ?? []).map(
      (item, index) => [item.id, `document-search-${index}`] as const,
    ),
    ...(documents?.groups ?? []).map(
      (item, index) => [item.id, `document-group-${index}`] as const,
    ),
    ...evidence.semantic_objects.map(
      (item, index) => [item.id, `semantic-object-${index}`] as const,
    ),
    ...evidence.datasets.map(
      (item, index) => [item.id, `semantic-dataset-${index}`] as const,
    ),
  ]);
  const normalizedEdges = evidence.edges
    .map((edge) => ({
      id: edge.id,
      source: nodeIds.get(edge.source),
      target: nodeIds.get(edge.target),
      data: { evidenceType: edge.type },
      ...edgeStyle(edge.type, edge.source, documents),
    }))
    .filter(
      (
        edge,
      ): edge is typeof edge & { source: string; target: string } => Boolean(edge.source && edge.target && edge.source !== edge.target),
    )
    .filter((edge, index, all) =>
      all.findIndex(
        (candidate) =>
          candidate.source === edge.source
          && candidate.target === edge.target
          && candidate.data.evidenceType === edge.data.evidenceType,
      ) === index
    );
  return {
    nodes,
    edges: normalizedEdges.length ? normalizedEdges : edges,
  };
}

const DOCUMENT_TOOLS = new Set(["search_evidence", "entity_claims"]);
const DOCUMENT_EDGES = new Set(["searched_documents", "found_passages"]);

type DocumentEvidence = NonNullable<SemanticTraceEvidence["documents"]>;

function documentTypeLabel(type: string, count: number): string {
  const names: Record<string, [string, string]> = {
    email: ["email", "emails"],
    chat: ["chat excerpt", "chat excerpts"],
    page: ["PDF page", "PDF pages"],
    claim: ["claim passage", "claim passages"],
  };
  const [one, many] = names[type] ?? [type, `${type} passages`];
  return `${count} ${count === 1 ? one : many}`;
}

/** "3 customer keys used" from the bridges of one document group. */
function bridgeLabel(bridges: DocumentEvidence["bridges"]): string {
  const classes = [...new Set(bridges.map((item) => item.entity.class).filter(Boolean))];
  const what = classes.length === 1 ? `${String(classes[0]).toLowerCase()} ` : "";
  return `${bridges.length} ${what}${bridges.length === 1 ? "key" : "keys"} used`;
}

function edgeStyle(
  type: string,
  source: string,
  documents: DocumentEvidence | null,
): Partial<Edge> {
  if (type === "key_used_in_query") {
    const bridges = documents?.bridges.filter((item) => item.group_id === source) ?? [];
    return {
      className: "trace-edge--bridge",
      animated: true,
      label: `${bridgeLabel(bridges)} as filters`,
    };
  }
  if (DOCUMENT_EDGES.has(type) || source.startsWith("documents:")) {
    return { className: "trace-edge--document" };
  }
  return {};
}

function addLaneLabel(
  nodes: Array<Node<TraceNodeData>>,
  id: string,
  row: number,
  label: string,
  lane: string,
) {
  nodes.push({
    id,
    type: "lane",
    position: { x: 2 * X_GAP, y: 80 + row * 210 - 44 },
    data: { label, lane } as unknown as TraceNodeData,
    selectable: false,
    draggable: false,
  });
}

function addNode(
  nodes: Array<Node<TraceNodeData>>,
  id: string,
  column: number,
  row: number,
  data: TraceNodeData,
) {
  nodes.push({
    id,
    type: "trace",
    position: { x: column * X_GAP, y: 80 + row * 210 },
    data,
  });
}

function connect(edges: Edge[], source: string, target: string) {
  edges.push({ id: `${source}-${target}`, source, target });
}

function friendlyToolName(name: string): string {
  const names: Record<string, string> = {
    search_semantics: "find business definitions",
    describe_model: "review the approved model",
    describe: "check a definition",
    compile_query: "prepare a safe data request",
    run_query: "retrieve approved data",
    explain_lineage: "check where data came from",
  };
  return names[name] || name.replaceAll("_", " ");
}

function unavailableFlow(detail: TraceDetail): TraceFlow {
  return {
    nodes: [{
      id: "semantic-unavailable",
      type: "trace",
      position: { x: 0, y: 80 },
      data: {
        label: "Answer path unavailable",
        detail: "This older trace does not include semantic evidence.",
        category: "result",
        status: detail.run.status,
        explanation:
          "Answer paths are available for answers created after semantic revision tracking was enabled.",
      },
    }],
    edges: [],
  };
}
