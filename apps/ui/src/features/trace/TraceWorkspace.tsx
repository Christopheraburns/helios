import {
  Background,
  Controls,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { TraceCollection, TraceDetail, TraceSpan } from "../../api/client";
import { ApplicationContextState } from "../../hooks/useApplicationContext";
import TraceNode from "./TraceNode";
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
  const [purpose, setPurpose] = useState<"" | "conversation" | "evaluation">(
    "",
  );
  const [includeAll, setIncludeAll] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

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
    () => detail ? adaptTraceToFlow(detail) : { nodes: [], edges: [] },
    [detail],
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
    <section className="trace-workspace" aria-labelledby="trace-workspace-title">
      <header className="trace-workspace__header">
        <div>
          <p className="page-header__eyebrow">Execution evidence</p>
          <h2 id="trace-workspace-title">MCP traces</h2>
          <p>
            Replay each LLM reasoning round and compare the client request with
            what the MCP server actually received.
          </p>
        </div>
        <button
          className="button button--secondary"
          type="button"
          disabled={loading}
          onClick={() => void loadRuns()}
        >
          Refresh traces
        </button>
      </header>

      <div className="trace-workspace__filters">
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
      </div>

      {error ? <p className="action-message action-message--error" role="alert">{error}</p> : null}
      <div className="trace-workspace__layout">
        <aside className="trace-workspace__runs" aria-label="Trace runs">
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
              nodes={flow.nodes}
              edges={flow.edges}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.25}
              maxZoom={1.6}
              onNodeClick={(_event, node) => {
                setSelectedSpan(
                  detail.spans.find((span) => span.id === node.id),
                );
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
          ) : detail ? (
            <>
              <h3>{detail.run.question_id || "Trace summary"}</h3>
              <p>{detail.run.question}</p>
              <dl>
                <div><dt>Model</dt><dd>{detail.run.llm_provider} · {detail.run.llm_model}</dd></div>
                <div><dt>Prompt</dt><dd>{detail.run.prompt_version}</dd></div>
                <div><dt>Tokens</dt><dd>{detail.run.tokens_in + detail.run.tokens_out}</dd></div>
              </dl>
              <p>Select a node to inspect its structured payload.</p>
            </>
          ) : (
            <p>Trace details appear here.</p>
          )}
        </aside>
      </div>
    </section>
  );
}
