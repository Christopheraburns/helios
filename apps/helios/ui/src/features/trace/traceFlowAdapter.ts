import { Edge, Node } from "@xyflow/react";

import { TraceDetail, TraceSpan } from "../../api/client";
import { TraceNodeData } from "./TraceNode";

export interface TraceFlow {
  nodes: Array<Node<TraceNodeData>>;
  edges: Edge[];
}

export function adaptTraceToFlow(detail: TraceDetail): TraceFlow {
  const nodes: Array<Node<TraceNodeData>> = [{
    id: "question",
    type: "trace",
    position: { x: 0, y: 100 },
    data: {
      label: detail.run.question_id || "Question",
      detail: detail.run.question,
      category: "question",
      status: detail.run.status,
    },
  }];
  const edges: Edge[] = [];
  const spans = [...detail.spans].sort(compareSpans);
  const mainSpans = spans.filter((span) => span.component === "agent");
  let previous = "question";
  mainSpans.forEach((span, index) => {
    nodes.push(spanNode(span, index + 1, 0));
    edges.push({
      id: `${previous}-${span.id}`,
      source: previous,
      target: span.id,
      className: span.status === "error" ? "trace-edge--error" : "",
    });
    previous = span.id;
    const children = spans.filter(
      (candidate) =>
        candidate.component === "mcp-server"
        && candidate.parent_span_id === span.id,
    );
    children.forEach((child, childIndex) => {
      nodes.push(spanNode(child, index + 1, childIndex + 1));
      edges.push({
        id: `${span.id}-${child.id}`,
        source: span.id,
        target: child.id,
        className: child.status === "error" ? "trace-edge--error" : "",
      });
    });
  });
  const orphanServerSpans = spans.filter(
    (span) =>
      span.component === "mcp-server"
      && !nodes.some((node) => node.id === span.id),
  );
  orphanServerSpans.forEach((span, index) => {
    nodes.push(spanNode(span, mainSpans.length + index + 1, 1));
    edges.push({
      id: `question-${span.id}`,
      source: "question",
      target: span.id,
    });
  });
  nodes.push({
    id: "result",
    type: "trace",
    position: { x: (mainSpans.length + 1) * 290, y: 100 },
    data: {
      label: detail.run.status === "completed" ? "Answer" : "Run ended",
      detail:
        detail.run.answer
        || detail.run.termination_reason
        || "No final answer was produced.",
      category: "result",
      status: detail.run.status,
      latencyMs: detail.run.duration_ms,
      tokens: detail.run.tokens_in + detail.run.tokens_out,
    },
  });
  edges.push({
    id: `${previous}-result`,
    source: previous,
    target: "result",
    className: detail.run.status === "failed" ? "trace-edge--error" : "",
  });
  return { nodes, edges };
}

function spanNode(
  span: TraceSpan,
  column: number,
  row: number,
): Node<TraceNodeData> {
  const round = Number(span.attributes.round || 0);
  const tokens = Number(span.attributes.tokens_in || 0)
    + Number(span.attributes.tokens_out || 0);
  const category: TraceNodeData["category"] =
    span.component === "mcp-server"
      ? "server"
      : span.kind === "llm"
        ? "llm"
        : "tool";
  return {
    id: span.id,
    type: "trace",
    position: {
      x: column * 290,
      y: row === 0 ? 100 : 330 + ((row - 1) * 210),
    },
    data: {
      label: span.name,
      detail:
        category === "llm"
          ? `Reasoning round ${round || column}`
          : category === "server"
            ? "Observed by MCP server"
            : "Requested by the LLM",
      category,
      status: span.status,
      latencyMs: span.latency_ms,
      tokens: tokens || undefined,
    },
  };
}

function compareSpans(left: TraceSpan, right: TraceSpan): number {
  return left.sequence - right.sequence
    || left.component.localeCompare(right.component)
    || left.id.localeCompare(right.id);
}
