import {
  Background,
  Controls,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { TraceCollection, TraceDetail, TraceSpan } from "../../api/client";
import { ApplicationContextState } from "../../hooks/useApplicationContext";
import TraceNode from "./TraceNode";
import { adaptSemanticPathToFlow } from "./semanticPathFlowAdapter";
import { adaptTraceToFlow } from "./traceFlowAdapter";

const nodeTypes = { trace: TraceNode };

export default function TraceWorkspace({
  context,
}: {
  context: ApplicationContextState;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [collection, setCollection] = useState<TraceCollection>();
  const [detail, setDetail] = useState<TraceDetail>();
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan>();
  const [selectedSemanticNode, setSelectedSemanticNode] = useState<string>();
  const [purpose, setPurpose] = useState<"" | "conversation" | "evaluation">(
    "",
  );
  const [includeAll, setIncludeAll] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [runsCollapsed, setRunsCollapsed] = useState(false);
  const view = searchParams.get("view") === "semantic"
    ? "semantic"
    : "execution";

  const loadRuns = useCallback(async () => {
    if (!context.selectedModelId) return;
    setLoading(true);
    setError("");
    try {
      const loaded = await context.loadModelTraces(
        context.selectedModelId,
        {
          purpose: purpose || undefined,
          includeAll,
        },
      );
      setCollection(loaded);
      const requested = searchParams.get("trace");
      const selected = loaded.items.find((item) => item.id === requested)
        ?? loaded.items[0];
      if (selected) {
        const trace = await context.loadModelTrace(
          context.selectedModelId,
          selected.id,
        );
        setDetail(trace);
        setSelectedSpan(undefined);
        setSelectedSemanticNode(undefined);
      } else {
        setDetail(undefined);
      }
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Trace runs could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [
    context.loadModelTrace,
    context.loadModelTraces,
    context.selectedModelId,
    includeAll,
    purpose,
    searchParams,
  ]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const flow = useMemo(
    () => detail
      ? view === "semantic"
        ? adaptSemanticPathToFlow(detail)
        : adaptTraceToFlow(detail)
      : { nodes: [], edges: [] },
    [detail, view],
  );
  const organizationAccess = collection?.available_actions.includes(
    "trace.read_organization",
  );

  async function selectRun(runId: string) {
    if (!context.selectedModelId) return;
    setLoading(true);
    setError("");
    try {
      const trace = await context.loadModelTrace(
        context.selectedModelId,
        runId,
      );
      setDetail(trace);
      setSelectedSpan(undefined);
      setSelectedSemanticNode(undefined);
      const next = new URLSearchParams(searchParams);
      next.set("tab", "traces");
      next.set("trace", runId);
      setSearchParams(next, { replace: true });
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Trace detail could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="trace-workspace" aria-label="MCP traces">
      <div className="trace-workspace__filters">
        <div className="trace-workspace__view-toggle" role="group" aria-label="Trace view">
          <button
            className={`button ${
              view === "semantic" ? "button--primary" : "button--secondary"
            }`}
            type="button"
            aria-pressed={view === "semantic"}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.set("view", "semantic");
              setSearchParams(next, { replace: true });
              setSelectedSpan(undefined);
            }}
          >
            Answer path
          </button>
          <button
            className={`button ${
              view === "execution" ? "button--primary" : "button--secondary"
            }`}
            type="button"
            aria-pressed={view === "execution"}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.set("view", "execution");
              setSearchParams(next, { replace: true });
              setSelectedSemanticNode(undefined);
            }}
          >
            Execution details
          </button>
        </div>
        <button
          className="button button--secondary"
          type="button"
          aria-controls="trace-run-list"
          aria-expanded={!runsCollapsed}
          onClick={() => setRunsCollapsed((collapsed) => !collapsed)}
        >
          {runsCollapsed ? "Show trace list" : "Hide trace list"}
        </button>
        <label>
          Purpose
          <select
            aria-label="Trace purpose"
            value={purpose}
            onChange={(event) => {
              setPurpose(event.currentTarget.value as typeof purpose);
            }}
          >
            <option value="">All runs</option>
            <option value="conversation">Conversations</option>
            <option value="evaluation">Evaluations</option>
          </select>
        </label>
        {organizationAccess ? (
          <label className="trace-workspace__checkbox">
            <input
              type="checkbox"
              checked={includeAll}
              onChange={(event) => setIncludeAll(event.currentTarget.checked)}
            />
            Organization traces
          </label>
        ) : null}
        <button
          className="button button--secondary trace-workspace__refresh"
          type="button"
          disabled={loading}
          onClick={() => void loadRuns()}
        >
          Refresh traces
        </button>
      </div>

      {error ? <p className="action-message action-message--error" role="alert">{error}</p> : null}
      <div className={`trace-workspace__layout${
        runsCollapsed ? " trace-workspace__layout--runs-collapsed" : ""
      }`}>
        <aside
          className="trace-workspace__runs"
          id="trace-run-list"
          aria-label="Trace runs"
          hidden={runsCollapsed}
        >
          {loading && !collection ? <p role="status">Loading traces…</p> : null}
          {collection?.items.map((run) => (
            <button
              type="button"
              key={run.id}
              className={detail?.run.id === run.id ? "is-active" : ""}
              onClick={() => void selectRun(run.id)}
            >
              <strong>{run.question_id || run.question}</strong>
              <span>{run.llm_provider} · {run.llm_model}</span>
              <span>{run.status} · {run.termination_reason || "in progress"}</span>
            </button>
          ))}
          {!loading && collection?.items.length === 0 ? (
            <p>No traces have been recorded for this model yet.</p>
          ) : null}
        </aside>

        <div className="trace-workspace__canvas">
          {detail ? (
            <ReactFlow
              key={`${view}-${runsCollapsed ? "runs-collapsed" : "runs-expanded"}`}
              nodes={flow.nodes}
              edges={flow.edges}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.25}
              maxZoom={1.6}
              style={{ width: "100%", height: "100%" }}
              onNodeClick={(_event, node) => {
                const span = detail.spans.find(
                  (item) => item.id === node.id,
                );
                setSelectedSpan(span);
                setSelectedSemanticNode(span ? undefined : node.id);
              }}
            >
              <Background />
              <Controls />
            </ReactFlow>
          ) : (
            <div className="trace-workspace__empty">
              Select a trace to open its execution canvas.
            </div>
          )}
        </div>

        <aside className="trace-workspace__inspector" aria-label="Trace inspector">
          {selectedSpan ? (
            <>
              <p className="page-header__eyebrow">{selectedSpan.component}</p>
              <h3>{selectedSpan.name}</h3>
              <dl>
                <div><dt>Status</dt><dd>{selectedSpan.status}</dd></div>
                <div><dt>Latency</dt><dd>{Math.round(selectedSpan.latency_ms || 0)} ms</dd></div>
                <div><dt>Sequence</dt><dd>{selectedSpan.sequence}</dd></div>
              </dl>
              {selectedSpan.error ? (
                <p className="action-message action-message--error">{selectedSpan.error}</p>
              ) : null}
              <details open>
                <summary>Input</summary>
                <pre>{JSON.stringify(selectedSpan.input, null, 2)}</pre>
              </details>
              <details>
                <summary>Output</summary>
                <pre>{JSON.stringify(selectedSpan.output, null, 2)}</pre>
              </details>
              <details>
                <summary>Attributes</summary>
                <pre>{JSON.stringify(selectedSpan.attributes, null, 2)}</pre>
              </details>
            </>
          ) : selectedSemanticNode && detail?.semantic_evidence ? (
            <SemanticInspector
              nodeId={selectedSemanticNode}
              detail={detail}
            />
          ) : detail ? (
            <>
              <h3>{view === "semantic" ? "Answer path" : detail.run.question_id || "Trace summary"}</h3>
              <p>{detail.run.question}</p>
              <dl>
                <div><dt>Model</dt><dd>{detail.run.llm_provider} · {detail.run.llm_model}</dd></div>
                <div><dt>Prompt</dt><dd>{detail.run.prompt_version}</dd></div>
                <div><dt>Tokens</dt><dd>{detail.run.tokens_in + detail.run.tokens_out}</dd></div>
                {view === "semantic" ? (
                  <>
                    <div>
                      <dt>Definition version</dt>
                      <dd>{detail.semantic_evidence?.revision?.id.slice(0, 12) || "Unavailable"}</dd>
                    </div>
                    <div>
                      <dt>Integrity</dt>
                      <dd>{detail.semantic_evidence?.revision?.verified ? "Verified" : "Incomplete"}</dd>
                    </div>
                  </>
                ) : null}
              </dl>
              <p>Select a step to see its supporting details.</p>
            </>
          ) : (
            <p>Trace details appear here.</p>
          )}
        </aside>
      </div>
    </section>
  );
}

function SemanticInspector({
  nodeId,
  detail,
}: {
  nodeId: string;
  detail: TraceDetail;
}) {
  const evidence = detail.semantic_evidence!;
  const objectIndex = nodeId.startsWith("semantic-object-")
    ? Number(nodeId.replace("semantic-object-", ""))
    : -1;
  const datasetIndex = nodeId.startsWith("semantic-dataset-")
    ? Number(nodeId.replace("semantic-dataset-", ""))
    : -1;
  const semanticObject = evidence.semantic_objects[objectIndex];
  const dataset = evidence.datasets[datasetIndex];
  const payload = semanticObject
    || dataset
    || (nodeId === "semantic-query" ? evidence.query : null)
    || (nodeId === "semantic-tools" ? evidence.tools : null)
    || (nodeId === "semantic-assistant" ? evidence.assistant : null)
    || (nodeId === "semantic-revision" ? evidence.revision : null)
    || (nodeId === "semantic-answer"
      ? { answer: evidence.answer, error: evidence.error }
      : null)
    || { question: evidence.question };
  const title = semanticObject?.name
    || dataset?.semantic_dataset
    || (nodeId === "semantic-revision" ? "Verified definition version" : null)
    || (nodeId === "semantic-query" ? "Approved query" : "Answer path step");

  return (
    <>
      <p className="page-header__eyebrow">Answer path</p>
      <h3>{title}</h3>
      {semanticObject?.description ? <p>{semanticObject.description}</p> : null}
      {dataset ? (
        <dl>
          <div><dt>Database table</dt><dd>{dataset.physical_name}</dd></div>
          <div><dt>Data source</dt><dd>{dataset.data_source_id || "Not recorded"}</dd></div>
        </dl>
      ) : null}
      {dataset?.data_source_id ? (
        <Link
          className="text-link"
          to={`/canvas?${new URLSearchParams({
            organization: detail.run.organization_id,
            model: detail.run.model_id,
            element_id: `datasource:${dataset.data_source_id}`,
            focus_node_id: `datasource:${dataset.data_source_id}`,
          })}`}
        >
          View this source in the semantic model
        </Link>
      ) : null}
      {semanticObject?.canvas_element_id ? (
        <Link
          className="text-link"
          to={`/canvas?${new URLSearchParams({
            organization: detail.run.organization_id,
            model: detail.run.model_id,
            element_id: semanticObject.canvas_element_id,
            focus_node_id: semanticObject.canvas_element_id,
          })}`}
        >
          View this definition in the semantic model
        </Link>
      ) : null}
      <details>
        <summary>Technical evidence</summary>
        <pre>{JSON.stringify(payload, null, 2)}</pre>
      </details>
      {evidence.revision ? (
        <details>
          <summary>Verified definition version</summary>
          <pre>{JSON.stringify(evidence.revision, null, 2)}</pre>
        </details>
      ) : null}
    </>
  );
}
