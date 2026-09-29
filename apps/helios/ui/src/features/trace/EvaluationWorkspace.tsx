import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { EvaluationRun } from "../../api/client";
import { ApplicationContextState } from "../../hooks/useApplicationContext";

const ACTIVE = new Set(["queued", "running"]);

export default function EvaluationWorkspace({
  context,
}: {
  context: ApplicationContextState;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [selected, setSelected] = useState<EvaluationRun>();
  const [repetitions, setRepetitions] = useState(3);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!context.selectedModelId) return;
    try {
      const collection = await context.loadModelEvaluations(
        context.selectedModelId,
      );
      setRuns(collection.items);
      const requested = searchParams.get("evaluation");
      const summary = collection.items.find((item) => item.id === requested)
        ?? collection.items[0];
      if (summary) {
        setSelected(await context.loadModelEvaluation(
          context.selectedModelId,
          summary.id,
        ));
      } else {
        setSelected(undefined);
      }
      setMessage("");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Evaluations could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [
    context.loadModelEvaluation,
    context.loadModelEvaluations,
    context.selectedModelId,
    searchParams,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected || !ACTIVE.has(selected.status)) return;
    const timer = window.setInterval(() => void load(), 5_000);
    return () => window.clearInterval(timer);
  }, [load, selected]);

  async function start() {
    if (!context.selectedModelId) return;
    setLoading(true);
    setMessage("");
    try {
      const created = await context.createModelEvaluation(
        context.selectedModelId,
        "tpcds",
        repetitions,
      );
      setRuns((current) => [created, ...current]);
      setSelected(created);
      const next = new URLSearchParams(searchParams);
      next.set("tab", "evaluations");
      next.set("evaluation", created.id);
      setSearchParams(next, { replace: true });
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "The evaluation could not be started.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function cancel() {
    if (!context.selectedModelId || !selected) return;
    const updated = await context.cancelModelEvaluation(
      context.selectedModelId,
      selected.id,
    );
    setSelected(updated);
    setMessage("Cancellation requested.");
  }

  async function selectRun(runId: string) {
    if (!context.selectedModelId) return;
    const detail = await context.loadModelEvaluation(
      context.selectedModelId,
      runId,
    );
    setSelected(detail);
    const next = new URLSearchParams(searchParams);
    next.set("evaluation", runId);
    setSearchParams(next, { replace: true });
  }

  function openTrace(traceId: string) {
    const next = new URLSearchParams(searchParams);
    next.set("tab", "traces");
    next.set("trace", traceId);
    setSearchParams(next);
  }

  const baseline = selected?.metrics.baseline;
  const candidate = selected?.metrics.candidate;

  return (
    <section className="evaluation-workspace" aria-labelledby="evaluation-title">
      <header className="trace-workspace__header">
        <div>
          <p className="page-header__eyebrow">Administrator evaluation</p>
          <h2 id="evaluation-title">Model comparison</h2>
          <p>
            Replay the versioned TPC-DS question set against the project model
            and your session override using identical prompts and tool limits.
          </p>
        </div>
        <div className="evaluation-workspace__start">
          <label>
            Repetitions
            <input
              aria-label="Evaluation repetitions"
              type="number"
              min={1}
              max={5}
              value={repetitions}
              onChange={(event) => setRepetitions(event.currentTarget.valueAsNumber)}
            />
          </label>
          <button
            className="button button--primary"
            type="button"
            disabled={loading || !Number.isInteger(repetitions)}
            onClick={() => void start()}
          >
            Run comparison
          </button>
        </div>
      </header>
      {message ? (
        <p
          className={`action-message action-message--${
            message.includes("requested") ? "success" : "error"
          }`}
          role="status"
        >
          {message}
        </p>
      ) : null}
      <div className="evaluation-workspace__layout">
        <aside className="trace-workspace__runs" aria-label="Evaluation runs">
          {runs.map((run) => (
            <button
              type="button"
              key={run.id}
              className={selected?.id === run.id ? "is-active" : ""}
              onClick={() => void selectRun(run.id)}
            >
              <strong>{run.suite_id} · {run.suite_version}</strong>
              <span>{run.status}</span>
              <span>{new Date(run.created_at).toLocaleString()}</span>
            </button>
          ))}
          {!loading && !runs.length ? <p>No evaluations have run yet.</p> : null}
        </aside>
        <div className="evaluation-workspace__detail">
          {selected ? (
            <>
              <div className="evaluation-workspace__summary">
                <div>
                  <span>Status</span>
                  <strong>{selected.status}</strong>
                </div>
                <div>
                  <span>Baseline</span>
                  <strong>{selected.baseline.provider} · {selected.baseline.model}</strong>
                </div>
                <div>
                  <span>Candidate</span>
                  <strong>{selected.candidate.provider} · {selected.candidate.model}</strong>
                </div>
                <div>
                  <span>Repetitions</span>
                  <strong>{selected.repetitions}</strong>
                </div>
              </div>
              {ACTIVE.has(selected.status) ? (
                <div className="evaluation-workspace__running" role="status">
                  <span className="spinner" aria-hidden="true" />
                  Evaluation is running. Completed question runs appear below.
                  <button
                    className="button button--secondary"
                    type="button"
                    disabled={selected.cancel_requested}
                    onClick={() => void cancel()}
                  >
                    {selected.cancel_requested ? "Cancelling…" : "Cancel"}
                  </button>
                </div>
              ) : null}
              {selected.error ? (
                <p className="action-message action-message--error">{selected.error}</p>
              ) : null}
              {baseline && candidate ? (
                <div className="evaluation-workspace__metrics">
                  <MetricCard title="Answer accuracy" baseline={baseline.answer_accuracy} candidate={candidate.answer_accuracy} percent />
                  <MetricCard title="Completion rate" baseline={baseline.completion_rate} candidate={candidate.completion_rate} percent />
                  <MetricCard title="Invalid-call rate" baseline={baseline.invalid_call_rate} candidate={candidate.invalid_call_rate} percent />
                  <MetricCard title="Redundant-call rate" baseline={baseline.redundant_call_rate} candidate={candidate.redundant_call_rate} percent />
                  <MetricCard title="Recovery rate" baseline={baseline.recovery_rate} candidate={candidate.recovery_rate} percent />
                  <MetricCard title="Grounding rate" baseline={baseline.grounding_rate} candidate={candidate.grounding_rate} percent />
                  <MetricCard title="Tokens / correct" baseline={baseline.tokens_per_correct_answer} candidate={candidate.tokens_per_correct_answer} />
                  <MetricCard title="Milliseconds / correct" baseline={baseline.milliseconds_per_correct_answer} candidate={candidate.milliseconds_per_correct_answer} />
                </div>
              ) : null}
              <div className="evaluation-workspace__results">
                {selected.results.map((result) => (
                  <button
                    type="button"
                    key={result.id}
                    onClick={() => openTrace(result.trace_run_id)}
                  >
                    <strong>{result.question_id}</strong>
                    <span>{result.variant} · repetition {result.repetition}</span>
                    <span className={result.accurate ? "is-correct" : "is-incorrect"}>
                      {result.accurate ? "Correct" : result.completed ? "Incorrect" : "Incomplete"}
                    </span>
                    <span>View trace</span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p>Select an evaluation or run a comparison.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function MetricCard({
  title,
  baseline,
  candidate,
  percent = false,
}: {
  title: string;
  baseline?: number;
  candidate?: number;
  percent?: boolean;
}) {
  const format = (value?: number) => {
    if (value == null) return "—";
    return percent ? `${Math.round(value * 100)}%` : Math.round(value).toLocaleString();
  };
  return (
    <article>
      <h3>{title}</h3>
      <dl>
        <div><dt>Project default</dt><dd>{format(baseline)}</dd></div>
        <div><dt>Session model</dt><dd>{format(candidate)}</dd></div>
      </dl>
    </article>
  );
}
