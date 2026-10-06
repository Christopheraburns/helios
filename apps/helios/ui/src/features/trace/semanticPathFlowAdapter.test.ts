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

  const documents = {
    searches: [
      { id: "span-search", tool: "search_evidence", query: "damaged packaging", status: "success", result_count: 3 },
    ],
    groups: [
      {
        id: "documents:email",
        type: "email",
        count: 2,
        search_ids: ["span-search"],
        passages: [
          {
            id: "seg-1",
            asset_id: "a1",
            text: "The box was crushed.",
            locator: { part: "body" },
            relevance: 0.58,
            entities: [{ class: "Customer", name: "Barbara Clark", keys: ["tpcds.customer:c_customer_sk=88705"] }],
          },
        ],
      },
      { id: "documents:chat", type: "chat", count: 1, search_ids: ["span-search"], passages: [] },
    ],
    bridges: [
      {
        id: "documents:email:tpcds.customer.c_customer_sk=88705",
        group_id: "documents:email",
        passage_id: "seg-1",
        key: "tpcds.customer:c_customer_sk=88705",
        field: "tpcds.customer.c_customer_sk",
        value: "88705",
        entity: { class: "Customer", name: "Barbara Clark" },
      },
    ],
  };
  const documentEdges = [
    { id: "d1", source: "assistant", target: "span-search", type: "searched_documents" },
    { id: "d2", source: "span-search", target: "documents:email", type: "found_passages" },
    { id: "d3", source: "span-search", target: "documents:chat", type: "found_passages" },
    { id: "d4", source: "documents:email", target: "answer", type: "supported_answer" },
    { id: "d5", source: "documents:chat", target: "answer", type: "supported_answer" },
  ];

  it("draws a document lane above the warehouse lane and bridges keys into the query", () => {
    const evidence = detail.semantic_evidence!;
    const flow = adaptSemanticPathToFlow({
      ...detail,
      semantic_evidence: {
        ...evidence,
        tools: [
          ...evidence.tools,
          { id: "span-search", name: "search_evidence", status: "success", error: null, arguments: {} },
        ],
        documents,
        edges: [
          ...evidence.edges,
          ...documentEdges,
          { id: "d6", source: "documents:email", target: "query", type: "key_used_in_query" },
        ],
      },
    });
    const node = (id: string) => flow.nodes.find((item) => item.id === id)!;

    expect(node("document-search-0").data.detail).toBe("“damaged packaging”");
    expect(node("document-group-0").data.label).toBe("2 emails");
    expect(node("document-group-0").data.detail).toBe("1 customer key used in the query");
    expect(node("document-group-1").data.label).toBe("1 chat excerpt");
    expect(node("semantic-tools").data.detail).not.toContain("search evidence");
    expect(node("document-search-0").position.y).toBeLessThan(node("semantic-tools").position.y);
    expect(flow.nodes.filter((item) => item.type === "lane").map((item) => item.id)).toEqual([
      "lane-documents",
      "lane-warehouse",
    ]);
    const bridge = flow.edges.find((edge) => edge.className === "trace-edge--bridge")!;
    expect([bridge.source, bridge.target, bridge.label]).toEqual([
      "document-group-0",
      "semantic-query",
      "1 customer key used as filters",
    ]);
    expect(
      flow.edges.some((edge) => edge.source === "document-group-1" && edge.target === "semantic-answer"),
    ).toBe(true);
  });

  it("shows only the document lane for an answer that used no warehouse data", () => {
    const evidence = detail.semantic_evidence!;
    const flow = adaptSemanticPathToFlow({
      ...detail,
      semantic_evidence: {
        ...evidence,
        tools: [{ id: "span-search", name: "search_evidence", status: "success", error: null, arguments: {} }],
        semantic_objects: [],
        datasets: [],
        query: null,
        documents: { ...documents, bridges: [] },
        edges: [
          { id: "q", source: "question", target: "assistant", type: "interpreted_by" },
          ...documentEdges,
        ],
      },
    });
    const ids = flow.nodes.map((item) => item.id);

    expect(ids).not.toContain("semantic-tools");
    expect(ids).not.toContain("semantic-dataset-0");
    expect(ids).not.toContain("lane-warehouse");
    expect(ids).toEqual(expect.arrayContaining(["lane-documents", "document-search-0", "document-group-0", "semantic-answer"]));
    expect(flow.nodes.find((item) => item.id === "document-group-0")!.data.detail)
      .toBe("Text given to the AI model for the answer");
    expect(flow.edges.every((edge) => ids.includes(edge.source) && ids.includes(edge.target))).toBe(true);
  });
});
