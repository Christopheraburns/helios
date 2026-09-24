import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { DiscoveryRun, DiscoveryRunPhase } from "../api/client";
import { EmptyState, ErrorState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface ModelsPageProps {
  context: ApplicationContextState;
}

export const RUN_POLL_INTERVAL_MS = 5_000;

export function isActiveRun(run: DiscoveryRun): boolean {
  return run.status === "queued" || run.status === "running";
}

export function isTerminalRun(run: DiscoveryRun): boolean {
  return [
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
  ].includes(run.status ?? "");
}

export default function ModelsPage({ context }: ModelsPageProps) {
  const location = useLocation();
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [loadState, setLoadState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const runsModelIdRef = useRef("");
  const lastRunsRef = useRef<DiscoveryRun[]>([]);
  const model = context.models.find(
    (item) => item.id === context.selectedModelId,
  );
  const organization = context.organizations.find(
    (item) => item.id === context.selectedOrganizationId,
  );

  useEffect(() => {
    if (!context.selectedModelId || context.modelStatus !== "ready") {
      setRuns([]);
      setLoadState("idle");
      setRefreshError("");
      runsModelIdRef.current = "";
      lastRunsRef.current = [];
      return;
    }
    let active = true;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    const modelId = context.selectedModelId;
    const hasCurrentData = runsModelIdRef.current === modelId;
    if (!hasCurrentData) {
      setRuns([]);
      setLoadState("loading");
      lastRunsRef.current = [];
    }
    setError("");
    setRefreshError("");

    async function loadRuns() {
      try {
        const result = await context.loadModelRuns(modelId);
        if (!active) return;
        const previousById = new Map(
          lastRunsRef.current.map((run) => [run.id, run]),
        );
        const reachedTerminalState = result.runs.some((run) => {
          const previous = previousById.get(run.id);
          return previous && isActiveRun(previous) && isTerminalRun(run);
        });
        runsModelIdRef.current = modelId;
        lastRunsRef.current = result.runs;
        setRuns(result.runs);
        setLoadState("ready");
        setRefreshError("");
        if (reachedTerminalState) context.refreshModelOverview();
        if (result.runs.some(isActiveRun)) {
          timeoutId = setTimeout(loadRuns, RUN_POLL_INTERVAL_MS);
        }
      } catch (reason) {
        if (!active) return;
        const message =
          reason instanceof Error
            ? reason.message
            : "Discovery activity is unavailable.";
        if (runsModelIdRef.current === modelId) {
          setLoadState("ready");
          setRefreshError(message);
        } else {
          setRuns([]);
          setLoadState("error");
          setError(message);
        }
      }
    }

    void loadRuns();
    return () => {
      active = false;
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    };
  }, [
    context.loadModelRuns,
    context.modelStatus,
    context.selectedModelId,
    retryVersion,
  ]);

  return (
    <>
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">{organization?.name ?? "Helios workspace"}</p>
          <h1>Discovery &amp; Activity</h1>
          <p>
            Artifact-backed discovery history for {model?.name ?? "the selected model"}.
          </p>
        </div>
        {model ? <span className="model-hero__id">{model.id}</span> : null}
      </header>

      {context.modelStatus === "loading" || loadState === "loading" ? (
        <div className="activity-loading" role="status">
          <span className="spinner" aria-hidden="true" />
          Loading discovery activity…
        </div>
      ) : context.modelStatus === "error" || loadState === "error" ? (
        <ErrorState
          title="Discovery activity could not be loaded"
          message={error || context.errorMessage || "The API request failed."}
          onRetry={() => setRetryVersion((version) => version + 1)}
        />
      ) : !model ? (
        <EmptyState
          title="No model selected"
          message="Select an accessible model to view its discovery activity."
        />
      ) : runs.length === 0 ? (
        <EmptyState
          title="No discovery activity"
          message="No discovery runs have been recorded for this model."
        />
      ) : (
        <>
          {refreshError ? (
            <RefreshError
              message={refreshError}
              onRetry={() => setRetryVersion((version) => version + 1)}
            />
          ) : null}
          <section className="activity-list" aria-label="Discovery runs">
            {runs.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                search={location.search}
              />
            ))}
          </section>
        </>
      )}
    </>
  );
}

export function RefreshError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="refresh-error" role="alert">
      <span>Automatic refresh failed: {message}</span>
      <button className="button button--secondary" onClick={onRetry} type="button">
        Retry refresh
      </button>
    </div>
  );
}

function RunCard({ run, search }: { run: DiscoveryRun; search: string }) {
  const phases = run.phases.filter(isDiscoveryPhase);
  return (
    <article className="run-card">
      <div className="run-card__header">
        <div>
          <p className="section-eyebrow">Discovery run</p>
          <h2>
            <Link
              to={{
                pathname: `/models/runs/${encodeURIComponent(run.id)}`,
                search,
              }}
            >
              {run.id}
            </Link>
          </h2>
        </div>
        <RunStatus status={run.status} missing={run.missing} />
      </div>

      <dl className="run-card__facts">
        <Fact label="Started" value={formatDate(run.started_at)} />
        <Fact label="Completed" value={formatDate(run.completed_at)} />
        <Fact label="Duration" value={formatDuration(run.duration_seconds)} />
        <Fact label="Initiator" value={available(run.initiator)} />
      </dl>

      <ol className="phase-strip" aria-label="Run phases">
        {phases.map((phase) => (
          <li key={phase.id} className={phase.available ? "phase-strip__complete" : ""}>
            <span aria-hidden="true" />
            <div>
              <strong>{phase.name}</strong>
              <small>{phase.available ? summarizeCounts(phase.counts) : "Unavailable"}</small>
            </div>
          </li>
        ))}
      </ol>

      <div className="run-card__footer">
        <span>{summarizeRunCounts(run)}</span>
        <Link
          className="text-link"
          to={{
            pathname: `/models/runs/${encodeURIComponent(run.id)}`,
            search,
          }}
        >
          View run details
        </Link>
      </div>
    </article>
  );
}

export function RunStatus({
  status,
  missing = false,
}: {
  status: DiscoveryRun["status"];
  missing?: boolean;
}) {
  const value = missing ? "unavailable" : status ?? "unavailable";
  return (
    <span className={`run-status run-status--${value}`}>
      <span aria-hidden="true" />
      {formatLabel(value)}
    </span>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function isDiscoveryPhase(phase: DiscoveryRunPhase): boolean {
  return ["harvest", "profile", "propose"].includes(phase.id);
}

export function summarizeCounts(counts: Record<string, number>): string {
  const values = Object.entries(counts);
  if (!values.length) return "No counts available";
  return values
    .map(([label, count]) => `${count} ${formatLabel(label).toLowerCase()}`)
    .join(" · ");
}

function summarizeRunCounts(run: DiscoveryRun): string {
  const tables =
    run.counts.discovered.tables ?? run.counts.profiled.tables;
  const proposals = Object.values(run.counts.proposed).reduce(
    (sum, count) => sum + count,
    0,
  );
  const parts = [];
  if (tables !== undefined) parts.push(`${tables} tables`);
  if (proposals > 0) parts.push(`${proposals} proposals`);
  return parts.length ? parts.join(" · ") : "Counts unavailable";
}

export function available(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 60) return `${Math.round(value)} sec`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes} min ${seconds} sec`;
}

export function formatLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
