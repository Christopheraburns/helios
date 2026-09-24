import { useEffect, useRef, useState } from "react";
import {
  Link,
  useLocation,
  useParams,
} from "react-router-dom";

import {
  DiscoveryRun,
  HistoricalProfileSummary,
  HistoricalTableProfile,
  RelationshipEvidence,
  TableColumnProfile,
} from "../api/client";
import { ErrorState } from "../components/AsyncState";
import ReviewWorkspace from "../components/ReviewWorkspace";
import { ApplicationContextState } from "../hooks/useApplicationContext";
import {
  RunStatus,
  RUN_POLL_INTERVAL_MS,
  RefreshError,
  available,
  formatDate,
  formatDuration,
  formatLabel,
  isActiveRun,
  isTerminalRun,
  summarizeCounts,
} from "./ModelsPage";

interface RunPageProps {
  context: ApplicationContextState;
}

export default function RunDetailPage({ context }: RunPageProps) {
  const { runId = "" } = useParams();
  const location = useLocation();
  const [run, setRun] = useState<DiscoveryRun>();
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const [profileSummary, setProfileSummary] =
    useState<HistoricalProfileSummary>();
  const [profileSummaryError, setProfileSummaryError] = useState("");
  const loadedRunKeyRef = useRef("");
  const lastRunRef = useRef<DiscoveryRun | undefined>(undefined);

  useEffect(() => {
    if (!context.selectedModelId || !runId) return;
    let active = true;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    const modelId = context.selectedModelId;
    const runKey = `${modelId}:${runId}`;
    const hasCurrentData = loadedRunKeyRef.current === runKey;
    if (!hasCurrentData) {
      setRun(undefined);
      setLoadState("loading");
      lastRunRef.current = undefined;
    }
    setError("");
    setRefreshError("");

    async function loadRun() {
      try {
        const result = await context.loadModelRun(modelId, runId);
        if (!active) return;
        const previous = lastRunRef.current;
        loadedRunKeyRef.current = runKey;
        lastRunRef.current = result;
        setRun(result);
        setLoadState("ready");
        setRefreshError("");
        if (
          previous &&
          isActiveRun(previous) &&
          isTerminalRun(result)
        ) {
          context.refreshModelOverview();
        }
        if (isActiveRun(result)) {
          timeoutId = setTimeout(loadRun, RUN_POLL_INTERVAL_MS);
        }
      } catch (reason) {
        if (!active) return;
        const message =
          reason instanceof Error ? reason.message : "Run details are unavailable.";
        if (loadedRunKeyRef.current === runKey) {
          setLoadState("ready");
          setRefreshError(message);
        } else {
          setRun(undefined);
          setLoadState("error");
          setError(message);
        }
      }
    }

    void loadRun();
    return () => {
      active = false;
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    };
  }, [
    context.loadModelRun,
    context.selectedModelId,
    retryVersion,
    runId,
  ]);

  useEffect(() => {
    const canLoad =
      context.selectedModelId &&
      runId &&
      run?.stages.profile &&
      run.available_actions?.includes("datasource.read");
    if (!canLoad) {
      setProfileSummary(undefined);
      setProfileSummaryError("");
      return;
    }
    let active = true;
    setProfileSummaryError("");
    void context
      .loadModelRunProfileSummary(context.selectedModelId, runId)
      .then((result) => {
        if (active) setProfileSummary(result);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setProfileSummary(undefined);
        setProfileSummaryError(
          reason instanceof Error
            ? reason.message
            : "Historical profile evidence is unavailable.",
        );
      });
    return () => {
      active = false;
    };
  }, [
    context.loadModelRunProfileSummary,
    context.selectedModelId,
    retryVersion,
    run?.available_actions,
    run?.stages.profile,
    runId,
  ]);

  if (loadState === "loading") {
    return (
      <div className="activity-loading" role="status">
        <span className="spinner" aria-hidden="true" />
        Loading run details…
      </div>
    );
  }
  if (loadState === "error" || !run) {
    return (
      <ErrorState
        title="Run details could not be loaded"
        message={error || "The API did not return this run."}
        onRetry={() => setRetryVersion((version) => version + 1)}
      />
    );
  }

  const phases = run.phases.filter((phase) =>
    ["harvest", "profile", "propose"].includes(phase.id),
  );
  const actions = new Set(run.available_actions ?? []);
  const canReadProfiles =
    run.stages.profile && actions.has("datasource.read");

  return (
    <>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to={{ pathname: "/models", search: location.search }}>
          Discovery &amp; Activity
        </Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{run.id}</span>
      </nav>
      <header className="page-header run-detail-header">
        <div>
          <p className="page-header__eyebrow">Discovery run</p>
          <h1>{run.id}</h1>
          <p>Historical artifacts and lifecycle evidence for this model-scoped run.</p>
        </div>
        <RunStatus status={run.status} />
      </header>

      {refreshError ? (
        <RefreshError
          message={refreshError}
          onRetry={() => setRetryVersion((version) => version + 1)}
        />
      ) : null}

      <section className="run-summary" aria-label="Run summary">
        <Summary label="Started" value={formatDate(run.started_at)} />
        <Summary label="Completed" value={formatDate(run.completed_at)} />
        <Summary label="Duration" value={formatDuration(run.duration_seconds)} />
        <Summary
          label="Progress"
          value={run.progress === null ? "—" : `${run.progress}%`}
        />
        <Summary label="Initiator" value={available(run.initiator)} />
        <Summary
          label="Source engine"
          value={available(run.data_source?.engine)}
        />
      </section>

      <section className="run-detail-panel" aria-labelledby="phases-title">
        <div className="run-detail-panel__header">
          <div>
            <p className="section-eyebrow">Pipeline evidence</p>
            <h2 id="phases-title">Phases</h2>
          </div>
          <span>{phases.filter((phase) => phase.available).length} of 3 available</span>
        </div>
        <ol className="phase-detail-list">
          {phases.map((phase) => (
            <li key={phase.id}>
              <div className="phase-detail-list__title">
                <span
                  className={phase.available ? "phase-marker phase-marker--complete" : "phase-marker"}
                  aria-hidden="true"
                />
                <div>
                  <h3>{phase.name}</h3>
                  <p>{phase.available ? "Completed" : "Artifact unavailable"}</p>
                </div>
              </div>
              <dl>
                <Summary label="Started" value={formatDate(phase.started_at)} />
                <Summary label="Completed" value={formatDate(phase.completed_at)} />
                <Summary label="Duration" value={formatDuration(phase.duration_seconds)} />
              </dl>
              <p className="phase-detail-list__counts">
                {phase.available ? summarizeCounts(phase.counts) : "—"}
              </p>
            </li>
          ))}
        </ol>
      </section>

      {run.stages.propose && actions.has("model.edit") && (
        <ReviewWorkspace context={context} runId={run.id} />
      )}

      <div className="run-detail-columns">
        <section className="run-detail-panel" aria-labelledby="source-title">
          <div className="run-detail-panel__header">
            <div>
              <p className="section-eyebrow">Source &amp; provenance</p>
              <h2 id="source-title">Execution context</h2>
            </div>
          </div>
          <dl className="detail-list">
            <Summary label="Engine" value={available(run.data_source?.engine)} />
            <Summary
              label="Databases"
              value={
                run.data_source?.databases.length
                  ? run.data_source.databases.join(", ")
                  : "—"
              }
            />
            <Summary label="Run type" value={available(run.type)} />
            <Summary label="Model ID" value={available(run.model_id)} />
          </dl>
          <Provenance value={run.provenance?.llm} />
        </section>

        <section className="run-detail-panel" aria-labelledby="issues-title">
          <div className="run-detail-panel__header">
            <div>
              <p className="section-eyebrow">Diagnostics</p>
              <h2 id="issues-title">Warnings &amp; errors</h2>
            </div>
          </div>
          <IssueList label="Warnings" items={run.warnings} />
          <IssueList label="Errors" items={run.errors} />
        </section>
      </div>

      <section className="run-detail-panel profile-access" aria-labelledby="profile-title">
        <div className="run-detail-panel__header">
          <div>
            <p className="section-eyebrow">Historical artifact</p>
            <h2 id="profile-title">Profiled tables</h2>
          </div>
          {profileSummary ? <span>{profileSummary.tables.length}</span> : null}
        </div>
        {canReadProfiles ? (
          profileSummaryError ? (
            <RefreshError
              message={profileSummaryError}
              onRetry={() => setRetryVersion((version) => version + 1)}
            />
          ) : profileSummary ? (
            <ProfileSummary
              summary={profileSummary}
              runId={run.id}
              search={location.search}
            />
          ) : (
            <div className="activity-loading" role="status">
              <span className="spinner" aria-hidden="true" />
              Loading historical profile evidence…
            </div>
          )
        ) : (
          <p>Historical table profiles are unavailable for this run or permission.</p>
        )}
      </section>
    </>
  );
}

function ProfileSummary({
  summary,
  runId,
  search,
}: {
  summary: HistoricalProfileSummary;
  runId: string;
  search: string;
}) {
  return (
    <>
      {summary.tables.length ? (
        <div className="profile-table-wrap">
          <table className="profile-table">
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">Columns</th>
                <th scope="col">Rows</th>
                <th scope="col">Primary key candidates</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {summary.tables.map((table) => (
                <tr key={table.table_id}>
                  <th scope="row" data-label="Table">
                    {table.profiled ? (
                      <Link
                        to={{
                          pathname: `/models/runs/${encodeURIComponent(runId)}/profile/${encodeURIComponent(table.table_id)}`,
                          search,
                        }}
                      >
                        {table.table_id}
                      </Link>
                    ) : (
                      table.table_id
                    )}
                  </th>
                  <td data-label="Columns">{formatNumber(table.column_count)}</td>
                  <td data-label="Rows">{formatNumber(table.row_count)}</td>
                  <td data-label="Primary keys">
                    {table.primary_key_candidates.map((item) => item.column).join(", ") || "—"}
                  </td>
                  <td data-label="Status">
                    {table.profiled ? "Profiled" : "Harvested"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="unavailable-message">No harvested or profiled tables were recorded.</p>
      )}
      <RelationshipGroup
        title="Accepted relationships"
        items={summary.relationships}
      />
      <RelationshipGroup
        title="Suggested relationships"
        items={summary.suggested_relationships}
      />
      <RelationshipGroup
        title="Rejected candidates"
        items={summary.rejected_candidates}
        showUnmatched
      />
    </>
  );
}

export function HistoricalTableProfilePage({ context }: RunPageProps) {
  const { runId = "", "*": tableId = "" } = useParams();
  const location = useLocation();
  const [profile, setProfile] = useState<HistoricalTableProfile>();
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [error, setError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    if (!context.selectedModelId || !runId || !tableId) return;
    let active = true;
    setLoadState("loading");
    void context
      .loadModelRunTableProfile(context.selectedModelId, runId, tableId)
      .then((result) => {
        if (!active) return;
        setProfile(result);
        setLoadState("ready");
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setProfile(undefined);
        setLoadState("error");
        setError(
          reason instanceof Error
            ? reason.message
            : "The historical profile is unavailable.",
        );
      });
    return () => {
      active = false;
    };
  }, [
    context.loadModelRunTableProfile,
    context.selectedModelId,
    retryVersion,
    runId,
    tableId,
  ]);

  if (loadState === "loading") {
    return (
      <div className="activity-loading" role="status">
        <span className="spinner" aria-hidden="true" />
        Loading historical table profile…
      </div>
    );
  }
  if (loadState === "error" || !profile) {
    return (
      <ErrorState
        title="Table profile could not be loaded"
        message={error || "The API did not return this historical profile."}
        onRetry={() => setRetryVersion((version) => version + 1)}
      />
    );
  }

  return (
    <>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to={{ pathname: "/models", search: location.search }}>Discovery &amp; Activity</Link>
        <span aria-hidden="true">/</span>
        <Link
          to={{
            pathname: `/models/runs/${encodeURIComponent(runId)}`,
            search: location.search,
          }}
        >
          {runId}
        </Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{profile.table_id}</span>
      </nav>
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">Historical table profile</p>
          <h1>{profile.table_id}</h1>
          <p>Profile artifact captured by run {profile.run_id}.</p>
        </div>
      </header>
      <section className="run-summary" aria-label="Table profile summary">
        <Summary label="Profiled" value={formatDate(profile.profiled_at)} />
        <Summary label="Engine" value={available(profile.engine)} />
        <Summary label="Rows" value={formatNumber(profile.row_count)} />
        <Summary label="Columns" value={formatNumber(profile.column_count)} />
        <Summary
          label="Relationships"
          value={formatNumber(profile.relationships.length)}
        />
        <Summary
          label="Primary keys"
          value={formatNumber(profile.primary_key_candidates.length)}
        />
      </section>
      <section className="run-detail-panel" aria-labelledby="primary-keys-title">
        <div className="run-detail-panel__header">
          <div>
            <p className="section-eyebrow">Candidate keys</p>
            <h2 id="primary-keys-title">Primary key candidates</h2>
          </div>
        </div>
        {profile.primary_key_candidates.length ? (
          <ul className="issue-group">
            {profile.primary_key_candidates.map((candidate) => (
              <li key={candidate.column}>
                <strong>{candidate.column}</strong>
                {candidate.confidence === undefined
                  ? ""
                  : ` · ${(candidate.confidence * 100).toFixed(0)}% confidence`}
              </li>
            ))}
          </ul>
        ) : (
          <p className="unavailable-message">No primary key candidates.</p>
        )}
      </section>
      <section className="run-detail-panel" aria-labelledby="columns-title">
        <div className="run-detail-panel__header">
          <div>
            <p className="section-eyebrow">Stored statistics</p>
            <h2 id="columns-title">Columns</h2>
          </div>
          <span>{profile.column_count}</span>
        </div>
        {Object.keys(profile.columns).length ? (
          <div className="profile-table-wrap">
            <table className="profile-table">
              <thead>
                <tr>
                  <th scope="col">Column</th>
                  <th scope="col">Type</th>
                  <th scope="col">Null rate</th>
                  <th scope="col">Distinct</th>
                  <th scope="col">Minimum</th>
                  <th scope="col">Maximum</th>
                  <th scope="col">Glossary terms</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(profile.columns).map(([name, column]) => (
                  <ProfileColumnRow key={name} name={name} column={column} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="unavailable-message">Column statistics are unavailable.</p>
        )}
        <p className="profile-provenance">
          Source: {profile.provenance.artifact} artifact · Run {profile.provenance.run_id}
        </p>
      </section>
      <section className="run-detail-panel" aria-labelledby="relationships-title">
        <div className="run-detail-panel__header">
          <div>
            <p className="section-eyebrow">Stored evidence</p>
            <h2 id="relationships-title">Accepted relationships</h2>
          </div>
        </div>
        <RelationshipTable items={profile.relationships} />
      </section>
    </>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function Provenance({ value }: { value?: Record<string, unknown> | null }) {
  if (!value || !Object.keys(value).length) {
    return <p className="unavailable-message">LLM provenance: —</p>;
  }
  return (
    <dl className="provenance-list">
      {Object.entries(value).map(([key, item]) => (
        <Summary key={key} label={formatLabel(key)} value={displayValue(item)} />
      ))}
    </dl>
  );
}

function IssueList({ label, items }: { label: string; items: unknown[] }) {
  return (
    <div className="issue-group">
      <h3>{label}</h3>
      {items.length ? (
        <ul>
          {items.map((item, index) => (
            <li key={index}>{displayValue(item)}</li>
          ))}
        </ul>
      ) : (
        <p>—</p>
      )}
    </div>
  );
}

function ProfileColumnRow({
  name,
  column,
}: {
  name: string;
  column: TableColumnProfile;
}) {
  return (
    <tr>
      <th scope="row" data-label="Column">{name}</th>
      <td data-label="Type">{available(column.type)}</td>
      <td data-label="Null rate">
        {typeof column.null_rate === "number"
          ? `${(column.null_rate * 100).toFixed(1)}%`
          : "—"}
      </td>
      <td data-label="Distinct">
        {column.ndv_exact !== undefined
          ? `${formatNumber(column.ndv_exact)} exact`
          : column.ndv !== undefined
            ? `≈ ${formatNumber(column.ndv)}`
            : "—"}
      </td>
      <td data-label="Minimum">{displayValue(column.min)}</td>
      <td data-label="Maximum">{displayValue(column.max)}</td>
      <td data-label="Glossary terms">
        {column.glossary_terms.join(", ") || "—"}
      </td>
    </tr>
  );
}

function RelationshipGroup({
  title,
  items,
  showUnmatched = false,
}: {
  title: string;
  items: RelationshipEvidence[];
  showUnmatched?: boolean;
}) {
  const id = title.toLowerCase().replace(/\s+/g, "-");
  return (
    <section className="profile-evidence" aria-labelledby={id}>
      <div className="run-detail-panel__header">
        <h3 id={id}>{title}</h3>
        <span>{items.length}</span>
      </div>
      <RelationshipTable items={items} showUnmatched={showUnmatched} />
    </section>
  );
}

function RelationshipTable({
  items,
  showUnmatched = false,
}: {
  items: RelationshipEvidence[];
  showUnmatched?: boolean;
}) {
  if (!items.length) {
    return <p className="unavailable-message">None.</p>;
  }
  return (
    <div className="profile-table-wrap">
      <table className="profile-table">
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">To</th>
            <th scope="col">Match</th>
            <th scope="col">{showUnmatched ? "Unmatched" : "Distinct values"}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={`${item.from}:${item.from_column}:${item.to}:${item.to_column}:${index}`}>
              <th scope="row" data-label="From">
                {qualifiedColumn(item.from, item.from_column)}
              </th>
              <td data-label="To">{qualifiedColumn(item.to, item.to_column)}</td>
              <td data-label="Match">
                {typeof item.match_ratio === "number"
                  ? `${(item.match_ratio * 100).toFixed(1)}%`
                  : "—"}
              </td>
              <td data-label={showUnmatched ? "Unmatched" : "Distinct values"}>
                {formatNumber(showUnmatched ? item.unmatched : item.distinct_values)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function qualifiedColumn(table: string, column?: string): string {
  return column ? `${table}.${column}` : table;
}

function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "—"
    : new Intl.NumberFormat().format(value);
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}
