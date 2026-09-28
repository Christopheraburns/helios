import { describe, expect, it } from "vitest";

import { TraceDetail } from "../../api/client";
import { adaptSemanticPathToFlow } from "./semanticPathFlowAdapter";

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
    question: "Customers by region?",
    llm_provider: "mistral",
    llm_model: "mistral-small-latest",
    prompt_version: "talk-v1",
    status: "completed",
    termination_reason: "final_answer",
    answer: "West has 12 customers.",
    started_at: "2026-09-28T00:00:00Z",
    completed_at: "2026-09-28T00:00:02Z",
    duration_ms: 2_000,
    tokens_in: 10,
    tokens_out: 5,
    semantic_revision_id: "a".repeat(64),
  },
  spans: [],
  semantic_evidence: {
    status: "complete",
    incomplete_reasons: [],
    question: "Customers by region?",
    assistant: {
      provider: "mistral",
      model: "mistral-small-latest",
      prompt_version: "talk-v1",
    },
    revision: {
      id: "a".repeat(64),
      sha256: "a".repeat(64),
      ossie_version: "0.2.0",
      discovery_run_id: "discovery-1",
      published_at: "2026-09-28T00:00:00Z",
      verified: true,
      verification_error: null,
    },
    tools: [{
      id: "tool-1",
      name: "run_query",
      status: "success",
      error: null,
      arguments: { metrics: ["Customer count"] },
    }],
    semantic_objects: [{
      id: `${"a".repeat(64)}#/metrics/0`,
      kind: "metric",
      name: "Customer count",
      description: "Number of customers",
      ossie_pointer: "/metrics/0",
    }],
    datasets: [{
      id: `${"a".repeat(64)}#physical:crm.customers`,
      semantic_dataset: "Customers",
      physical_name: "crm.customers",
      data_source_id: "warehouse",
    }],
    query: {
      id: "query",
      sql: "SELECT region, COUNT(*) FROM crm.customers",
      columns: ["region", "customer_count"],
      row_count: 1,
    },
    edges: [
      {
        id: "question-assistant",
        source: "question",
        target: "assistant",
        type: "interpreted_by",
      },
      {
        id: "assistant-tool",
        source: "assistant",
        target: "tool-1",
        type: "used_controlled_tool",
      },
      {
        id: "tool-object",
        source: "tool-1",
        target: `${"a".repeat(64)}#/metrics/0`,
        type: "resolved_definition",
      },
      {
        id: "object-dataset",
        source: `${"a".repeat(64)}#/metrics/0`,
        target: `${"a".repeat(64)}#physical:crm.customers`,
        type: "mapped_to_physical_data",
      },
      {
        id: "dataset-query",
        source: `${"a".repeat(64)}#physical:crm.customers`,
        target: "query",
        type: "used_in_query",
      },
      {
        id: "query-answer",
        source: "query",
        target: "answer",
        type: "supported_answer",
      },
    ],
    answer: "West has 12 customers.",
    error: null,
  },
};

describe("adaptSemanticPathToFlow", () => {
  it("maps the question through semantics and physical data to the answer", () => {
    const flow = adaptSemanticPathToFlow(detail);

    expect(flow.nodes.map((node) => node.data.label)).toEqual([
      "Your question",
      "Helios understands the request",
      "Helios checks approved definitions",
      "Business meaning",
      "Verified definition version",
      "Data source",
      "Approved query",
      "Answer",
    ]);
    expect(flow.edges).toContainEqual(expect.objectContaining({
      source: "semantic-object-0",
      target: "semantic-dataset-0",
    }));
    expect(flow.nodes[3].data.explanation).toContain(
      "approved business definition",
    );
  });

  it("shows an explicit incomplete path when evidence is unavailable", () => {
    const flow = adaptSemanticPathToFlow({
      ...detail,
      semantic_evidence: undefined,
    });
    expect(flow.nodes[0].data.label).toBe("Answer path unavailable");
  });

  it("represents a semantic-only answer without inventing a query", () => {
    const flow = adaptSemanticPathToFlow({
      ...detail,
      semantic_evidence: {
        ...detail.semantic_evidence!,
        datasets: [],
        query: null,
        edges: [
          {
            id: "object-answer",
            source: `${"a".repeat(64)}#/metrics/0`,
            target: "answer",
            type: "supported_answer",
          },
        ],
      },
    });

    expect(flow.nodes.some((node) => node.id === "semantic-query")).toBe(false);
    expect(flow.edges).toContainEqual(expect.objectContaining({
      source: "semantic-object-0",
      target: "semantic-answer",
    }));
  });

  it("ends an incomplete path at a clearly labeled failure", () => {
    const flow = adaptSemanticPathToFlow({
      ...detail,
      semantic_evidence: {
        ...detail.semantic_evidence!,
        status: "incomplete",
        incomplete_reasons: ["The request did not complete successfully."],
        semantic_objects: [],
        datasets: [],
        query: null,
        answer: null,
        error: {
          reason: "authorization_denied",
          message: "You do not have access to this data.",
        },
        edges: [{
          id: "tool-answer",
          source: "tool-1",
          target: "answer",
          type: "supported_answer",
        }],
      },
    });

    expect(flow.nodes.at(-1)?.data.label).toBe("Request ended");
    expect(flow.nodes.at(-1)?.data.detail).toContain("do not have access");
  });
});
