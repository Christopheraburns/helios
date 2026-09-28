import { describe, expect, it } from "vitest";

import { TraceDetail, TraceSpan } from "../../api/client";
import { adaptTraceToFlow } from "./traceFlowAdapter";

function span(
  id: string,
  sequence: number,
  component: string,
  kind: string,
  parentSpanId: string | null = null,
): TraceSpan {
  return {
    id,
    run_id: "run-1",
    parent_span_id: parentSpanId,
    sequence,
    component,
    kind,
    name: kind === "llm" ? "mistral.chat" : "run_query",
    status: "success",
    started_at: "2026-09-28T00:00:00Z",
    completed_at: "2026-09-28T00:00:01Z",
    latency_ms: 100,
    input: {},
    output: {},
    attributes: { round: 1, tokens_in: 10, tokens_out: 5 },
    error: null,
  };
}

describe("adaptTraceToFlow", () => {
  it("links question, agent rounds, server observations, and answer", () => {
    const detail: TraceDetail = {
      run: {
        id: "run-1",
        request_id: "request-1",
        conversation_id: "conversation-1",
        principal_id: "principal-1",
        organization_id: "acme",
        model_id: "customer360",
        purpose: "conversation",
        question_id: null,
        question: "Revenue by channel?",
        llm_provider: "mistral",
        llm_model: "mistral-small-latest",
        prompt_version: "talk-v1",
        status: "completed",
        termination_reason: "final_answer",
        answer: "Revenue was highest online.",
        started_at: "2026-09-28T00:00:00Z",
        completed_at: "2026-09-28T00:00:02Z",
        duration_ms: 2_000,
        tokens_in: 10,
        tokens_out: 5,
      },
      spans: [
        span("llm-1", 1, "agent", "llm"),
        span("tool-1", 2, "agent", "tool"),
        span("server-1", 2, "mcp-server", "tool", "tool-1"),
      ],
    };

    const flow = adaptTraceToFlow(detail);

    expect(flow.nodes.map((node) => node.id)).toEqual([
      "question",
      "llm-1",
      "tool-1",
      "server-1",
      "result",
    ]);
    expect(flow.edges.map((edge) => [edge.source, edge.target])).toContainEqual(
      ["tool-1", "server-1"],
    );
    expect(flow.edges.at(-1)).toMatchObject({
      source: "tool-1",
      target: "result",
    });
  });
});
