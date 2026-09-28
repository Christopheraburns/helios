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

  const toolDetail = evidence.tools.length
    ? evidence.tools.map((tool) => friendlyToolName(tool.name)).join(", ")
    : "No data tools were recorded";
  addNode(nodes, "semantic-tools", 2, 0, {
    label: "Helios checks approved definitions",
    detail: toolDetail,
    category: "tool",
    status: evidence.tools.some((tool) => tool.status === "error")
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

  let previousIds = dataSets.map((_item, index) => `semantic-dataset-${index}`);
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
    ...evidence.tools.map((tool) => [tool.id, "semantic-tools"] as const),
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
    }))
    .filter(
      (
        edge,
      ): edge is {
        id: string;
        source: string;
        target: string;
        data: { evidenceType: string };
      } => Boolean(edge.source && edge.target && edge.source !== edge.target),
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
