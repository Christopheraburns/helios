import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AuthorizationError,
  type CrawlEvaluation,
  type CrawlEvaluationNumbers,
  type CrawlLaunch,
  type CrawlLaunches,
  type CrawlRunDetail,
  type CrawlRunSummary,
  type CrawlStrategy,
  type CrawlTarget,
  type CrawlerSettingsSaveResult,
  type CrawlerSettingsState,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import "./CrawlerPage.css";

interface CrawlerPageProps {
  context: ApplicationContextState;
}

type Tab = "runs" | "scores" | "settings";
const TABS: { id: Tab; label: string }[] = [
  { id: "runs", label: "Runs" },
  { id: "scores", label: "Scores" },
  { id: "settings", label: "Settings" },
];

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

const short = (value: string | null | undefined, n = 12) =>
  value ? (value.length > n ? `${value.slice(0, n)}…` : value) : "—";

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");

const pct = (value: number | null | undefined) => (value == null ? "—" : value.toFixed(2));

/** "direct 0.97 · claims 0.91" from an evaluation's headline numbers. */
function scoreLabel(summary: CrawlEvaluationNumbers | undefined): string {
  if (!summary) return "—";
  return `direct ${pct(summary.mention_recall_direct)} · claims ${pct(summary.claim_recall)}`;
}

/** The dataset a run was crawled from, when it is a Helios-DS dataset. */
function datasetOf(run: CrawlRunSummary): string {
  const source = run.settings?.data_source as { connector?: string; scope?: { dataset_id?: string } } | undefined;
  if (run.connector.startsWith("helios_ds") || source?.connector?.startsWith("helios_ds")) {
    return source?.scope?.dataset_id ?? run.source;
  }
  return "";
}

function duration(run: CrawlRunSummary): string {
  if (!run.finished_at) return run.status === "RUNNING" ? "running" : "—";
  const seconds = (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000;
  return seconds < 120 ? `${seconds.toFixed(0)} s` : `${(seconds / 60).toFixed(1)} min`;
}

// Asset statuses that mean the asset was usable in the run.
const GOOD_STATUSES = new Set(["fetched", "carried_forward", "analyzed"]);

// Asset statuses that mean a document could not be read or analyzed. A run's
// counts also hold totals of what was found (mentions, claims, ...), which are
// not problems.
const PROBLEM_STATUSES = [
  "integrity_failed",
  "missing",
  "fetch_failed",
  "too_large",
  "unsupported",
  "type_mismatch",
  "no_text",
  "invalid",
  "embedding_failed",
  "llm_failed",
  "llm_invalid_replies",
];

function problemCount(counts: Record<string, number>): number {
  return PROBLEM_STATUSES.reduce((total, status) => total + (counts[status] ?? 0), 0);
}

// What the analysis produced, in the order the pipeline produces it.
const FOUND_COUNTS: { key: string; label: string }[] = [
  { key: "segments", label: "segments" },
  { key: "mentions", label: "mentions" },
  { key: "entities", label: "entities" },
  { key: "links_sameas", label: "definite links" },
  { key: "links_possible", label: "possible links" },
  { key: "relationships", label: "relationships" },
  { key: "cases", label: "cases" },
  { key: "cases_resolved", label: "cases matched to a warehouse row" },
  { key: "claims", label: "claims" },
  { key: "claim_evidence", label: "evidence passages" },
  { key: "embedded", label: "searchable passages" },
];

const STRATEGY_LABELS: Record<string, string> = {
  deterministic: "Rules",
  llm: "LLM",
};

const strategyOf = (run: { strategy?: string }) => run.strategy ?? "deterministic";

/** An LLM crawl's cost and reliability, from its counts. */
function llmFacts(counts: Record<string, number>): string {
  const n = (key: string) => counts[key] ?? 0;
  const parts = [
    `${(n("llm_calls") + n("llm_cached")).toLocaleString()} documents read (${n("llm_cached").toLocaleString()} from cache)`,
    `${n("llm_tokens_in").toLocaleString()} tokens in, ${n("llm_tokens_out").toLocaleString()} out`,
  ];
  if (counts.llm_cost_microusd != null) parts.push(`$${(counts.llm_cost_microusd / 1e6).toFixed(2)}`);
  parts.push(`${n("llm_hallucinated_spans").toLocaleString()} quotes not in the documents (dropped)`);
  if (n("llm_claims_unanchored")) {
    parts.push(`${n("llm_claims_unanchored").toLocaleString()} claims without a resolved subject or object (dropped)`);
  }
  if (n("llm_failed")) parts.push(`${n("llm_failed")} documents failed`);
  if (n("llm_invalid_replies")) parts.push(`${n("llm_invalid_replies")} unreadable replies`);
  return parts.join(" · ");
}

function statusClass(status: string): string {
  if (status === "SUCCEEDED" || GOOD_STATUSES.has(status)) return "crawler-badge--ok";
  if (status === "RUNNING") return "crawler-badge--running";
  return "crawler-badge--problem";
}

export default function CrawlerPage({ context }: CrawlerPageProps) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab: Tab = requested === "settings" || requested === "scores" ? requested : "runs";
  const setTab = (next: Tab) => {
    const updated = new URLSearchParams(params);
    updated.set("tab", next);
    setParams(updated, { replace: true });
  };

  return (
    <div className="crawler-page">
      <div className="crawler-header">
        <h1>Crawler</h1>
        <p className="crawler-subtitle">
          What the crawler read, what it found, and the settings it runs with. Start a crawl on
          the Runs tab; it runs in the background as a Workbench Job.{" "}
          <Link to="/docs/helios/crawler-guide">Read the guide</Link>
        </p>
        <div className="crawler-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={`crawler-tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tab === "runs" && <RunsTab context={context} />}
      {tab === "scores" && <ScoresTab context={context} />}
      {tab === "settings" && <SettingsTab context={context} />}
    </div>
  );
}

// --- runs --------------------------------------------------------------------------

function RunsTab({ context }: CrawlerPageProps) {
  const { crawlerClient } = context;
  const [runs, setRuns] = useState<CrawlRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [strategy, setStrategy] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRuns(await crawlerClient().crawlRuns!());
    } catch (err) {
      setError(message(err, "Failed to load crawl runs"));
    }
  }, [crawlerClient]);

  useEffect(() => {
    void load();
  }, [load]);

  const [launches, setLaunches] = useState<CrawlLaunches | null>(null);
  const loadLaunches = useCallback(async () => {
    try {
      setLaunches((await crawlerClient().crawlLaunches?.()) ?? null);
    } catch {
      setLaunches(null); // the runs table still works without launch status
    }
  }, [crawlerClient]);

  useEffect(() => {
    void loadLaunches();
  }, [loadLaunches]);

  // While a crawl is starting or running, keep its status and the runs table current.
  const crawling = (launches?.launches ?? []).some((l) => l.active);
  useEffect(() => {
    if (!crawling) return;
    const timer = window.setInterval(() => {
      void loadLaunches();
      void load();
    }, 8000);
    return () => window.clearInterval(timer);
  }, [crawling, load, loadLaunches]);

  const sources = useMemo(() => Array.from(new Set((runs ?? []).map((r) => r.source))).sort(), [runs]);
  const strategies = useMemo(
    () => Array.from(new Set((runs ?? []).map(strategyOf))).sort(),
    [runs],
  );
  const shown = (runs ?? []).filter(
    (r) => (!source || r.source === source) && (!strategy || strategyOf(r) === strategy),
  );

  if (error) return <ErrorState title="Failed to load crawl runs" message={error} />;
  if (!runs) return <LoadingState />;

  return (
    <div className="crawler-runs">
      <div className="crawler-toolbar">
        <label>
          Source{" "}
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All ({runs.length} runs)</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {short(s, 13)}
              </option>
            ))}
          </select>
        </label>
        <label title="How the text was understood: by rules and the warehouse, or by an LLM">
          Strategy{" "}
          <select
            aria-label="Filter by strategy"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            <option value="">All</option>
            {strategies.map((s) => (
              <option key={s} value={s}>
                {STRATEGY_LABELS[s] ?? s}
              </option>
            ))}
          </select>
        </label>
        <button
          className="crawler-button"
          onClick={() => {
            void load();
            void loadLaunches();
          }}
        >
          Refresh
        </button>
      </div>

      <StartCrawl
        context={context}
        launches={launches}
        onStarted={() => {
          void loadLaunches();
          void load();
        }}
      />

      {shown.length === 0 ? (
        <div className="crawler-empty">No crawl runs yet. Use Start crawl above to create one.</div>
      ) : (
        <table className="crawler-table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Source</th>
              <th>Status</th>
              <th className="num">Listed</th>
              <th className="num" title="Fetched, verified and split into segments in this run">Analyzed</th>
              <th className="num">Carried forward</th>
              <th className="num">Problems</th>
              <th className="num" title="Addressable parts: PDF pages, email parts, chat messages">Segments</th>
              <th>Took</th>
              <th>Ontology</th>
              <th>Settings</th>
              <th title="The user the crawl connected as">Ran as</th>
              <th title="Latest evaluation: direct-mention recall and claim recall">Score</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((run) => (
              <tr
                key={run.crawl_run_id}
                className={`clickable ${selected === run.crawl_run_id ? "selected" : ""}`}
                onClick={() => setSelected(selected === run.crawl_run_id ? null : run.crawl_run_id)}
              >
                <td className="nowrap">{when(run.started_at)}</td>
                <td className="mono">
                  {short(run.source, 13)}
                  {run.note && <div className="crawler-note-line">{run.note}</div>}
                </td>
                <td>
                  <span className={`crawler-badge ${statusClass(run.status)}`}>{run.status}</span>
                  {strategyOf(run) !== "deterministic" && (
                    <span
                      className="crawler-badge crawler-badge--strategy"
                      title={run.llm ? `${run.llm.provider} ${run.llm.model}` : undefined}
                    >
                      {STRATEGY_LABELS[strategyOf(run)] ?? strategyOf(run)}
                    </span>
                  )}
                </td>
                <td className="num">{run.counts.listed ?? "—"}</td>
                <td className="num">{run.counts.analyzed ?? run.counts.fetched ?? 0}</td>
                <td className="num">{run.counts.carried_forward ?? 0}</td>
                <td className={`num ${problemCount(run.counts) ? "crawler-problem" : ""}`}>
                  {problemCount(run.counts)}
                </td>
                <td className="num">{run.counts.segments ?? "—"}</td>
                <td className="nowrap">{duration(run)}</td>
                <td>{run.ontology_version}</td>
                <td>{run.settings_version ?? "defaults"}</td>
                <td className="nowrap">
                  {run.actor}
                  {run.isolated === false && (
                    <span
                      className="crawler-badge crawler-badge--problem crawler-badge--inline"
                      title="This crawl did not connect as the crawler machine user, so its access to the ground truth was not restricted."
                    >
                      not isolated
                    </span>
                  )}
                </td>
                <td className="nowrap crawler-score">{scoreLabel(run.latest_evaluation?.summary)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && <RunDetail context={context} crawlRunId={selected} />}
    </div>
  );
}

const launchTarget = (launch: CrawlLaunch) => launch.source_id ?? launch.dataset_id ?? "unknown";

/** Starts a crawl (a run of the Workbench crawl Job) and shows crawls in progress. */
function StartCrawl({
  context,
  launches,
  onStarted,
}: CrawlerPageProps & { launches: CrawlLaunches | null; onStarted: () => void }) {
  const { crawlerClient } = context;
  const [targets, setTargets] = useState<CrawlTarget[]>([]);
  const [targetKey, setTargetKey] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [full, setFull] = useState(false);
  const [strategy, setStrategy] = useState<CrawlStrategy>("deterministic");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const loaded = (await crawlerClient().crawlTargets?.()) ?? [];
        if (!active) return;
        setTargets(loaded);
        setTargetKey(loaded.length ? `${loaded[0].kind}:${loaded[0].id}` : "other");
      } catch {
        if (active) setTargetKey("other");
      }
    })();
    return () => {
      active = false;
    };
  }, [crawlerClient]);

  if (!launches) return null;
  if (!launches.available) {
    return (
      <div className="crawler-start crawler-start--unavailable">
        Crawls can't be started from here: {launches.reason}
      </div>
    );
  }

  const chosen: CrawlTarget | null =
    targetKey === "other"
      ? datasetId.trim()
        ? { kind: "dataset", id: datasetId.trim(), label: datasetId.trim(), connector: "helios_ds" }
        : null
      : targets.find((t) => `${t.kind}:${t.id}` === targetKey) ?? null;
  const active = launches.launches.filter((l) => l.active);
  const alreadyRunning = chosen ? active.some((l) => launchTarget(l) === chosen.id) : false;
  const lastFinished = launches.launches.find((l) => !l.active);

  const start = async () => {
    if (!chosen) return;
    setBusy(true);
    setFeedback(null);
    try {
      const launched = await crawlerClient().startCrawl!(
        chosen,
        full,
        note.trim() || undefined,
        strategy,
      );
      setNote("");
      setFeedback({
        kind: "ok",
        text: `Crawl requested (Workbench job run ${launched.job_run_id}). It appears below once its container has started.`,
      });
      onStarted();
    } catch (err) {
      setFeedback({ kind: "error", text: message(err, "The crawl could not be started") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="crawler-start">
      <div className="crawler-start__form">
        <label>
          Crawl{" "}
          <select value={targetKey} onChange={(e) => setTargetKey(e.target.value)}>
            {targets.map((t) => (
              <option key={`${t.kind}:${t.id}`} value={`${t.kind}:${t.id}`}>
                {t.kind === "dataset" ? `Helios-DS dataset ${short(t.id, 13)}` : t.label}
              </option>
            ))}
            <option value="other">Another Helios-DS dataset…</option>
          </select>
        </label>
        {targetKey === "other" && (
          <input
            aria-label="Helios-DS dataset ID"
            placeholder="Dataset ID"
            value={datasetId}
            maxLength={128}
            onChange={(e) => setDatasetId(e.target.value)}
          />
        )}
        <label title="Re-read and re-analyze every document, even if unchanged since the last crawl">
          <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Full
          re-crawl
        </label>
        <label title="Rules: the standard crawler, which feeds search. LLM: an experiment that reads each document with the project's AI model, to be scored against the rules; it uses the model's quota and does not feed search.">
          Strategy{" "}
          <select
            aria-label="Crawl strategy"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as CrawlStrategy)}
          >
            <option value="deterministic">Rules (standard)</option>
            <option value="llm">LLM (experiment)</option>
          </select>
        </label>
        <input
          className="crawler-start__note"
          aria-label="Note for this crawl"
          placeholder="Note (optional): a label to recognise this crawl by"
          value={note}
          maxLength={200}
          onChange={(e) => setNote(e.target.value)}
        />
        <button
          className="crawler-button crawler-button--primary"
          disabled={busy || !chosen || alreadyRunning}
          onClick={() => void start()}
        >
          {busy ? "Starting…" : "Start crawl"}
        </button>
      </div>
      {active.map((l) => (
        <div className="crawler-start__status" role="status" key={l.job_run_id}>
          <span className="crawler-badge crawler-badge--running">{l.status.replace("ENGINE_", "")}</span>{" "}
          {l.strategy === "llm" ? "LLM crawl" : "Crawl"} of {short(launchTarget(l), 13)} requested {when(l.created_at)}
          {l.requested_by ? ` by ${l.requested_by.split(":").pop()}` : ""} (job run {l.job_run_id})
          {l.note ? `: ${l.note}` : ""}
        </div>
      ))}
      {!active.length && lastFinished && !lastFinished.status.endsWith("SUCCEEDED") && (
        <div className="crawler-start__status">
          <span className="crawler-badge crawler-badge--problem">{lastFinished.status.replace("ENGINE_", "")}</span>{" "}
          The last crawl job run ({lastFinished.job_run_id}) did not succeed. Its log is under
          Jobs → {launches.job_name} in Workbench.
        </div>
      )}
      {feedback && (
        <div className={feedback.kind === "error" ? "crawler-error" : "crawler-start__status"} role="status">
          {feedback.text}
        </div>
      )}
    </div>
  );
}

function RunDetail({ context, crawlRunId }: CrawlerPageProps & { crawlRunId: string }) {
  const { crawlerClient } = context;
  const [run, setRun] = useState<CrawlRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    setRun(null);
    setError(null);
    crawlerClient()
      .crawlRun!(crawlRunId)
      .then((data) => !cancelled && setRun(data))
      .catch((err) => !cancelled && setError(message(err, "Failed to load the run")));
    return () => {
      cancelled = true;
    };
  }, [crawlerClient, crawlRunId]);

  if (error) return <ErrorState title="Failed to load the run" message={error} />;
  if (!run) return <LoadingState />;

  const needle = query.trim().toLowerCase();
  const assets = run.assets.filter(
    (a) =>
      (!status || a.status === status) &&
      (!kind || a.ontology_class === kind) &&
      (!needle || a.asset_id.toLowerCase().includes(needle) || (a.object_key ?? "").toLowerCase().includes(needle)),
  );

  return (
    <section className="crawler-detail">
      <div className="crawler-detail-header">
        <h2 className="mono">{run.crawl_run_id}</h2>
        <span className={`crawler-badge ${statusClass(run.status)}`}>{run.status}</span>
      </div>
      <dl className="crawler-facts">
        <dt>Source</dt>
        <dd className="mono">{run.source}</dd>
        <dt>Run by</dt>
        <dd>{run.actor}</dd>
        <dt>Started / finished</dt>
        <dd>
          {when(run.started_at)} / {when(run.finished_at)} ({duration(run)})
        </dd>
        <dt>Crawler / ontology / settings</dt>
        <dd>
          {run.crawler_version} / {run.ontology_version} / {run.settings_version ?? "built-in defaults"}{" "}
          <span className="mono muted">{short(run.settings_hash, 10)}</span>
        </dd>
        <dt>Strategy</dt>
        <dd>
          {strategyOf(run) === "llm"
            ? "LLM: each document read by an AI model; links by exact keys only"
            : "Rules: patterns, warehouse names and joint resolution"}
          {run.llm && (
            <>
              {" · "}
              {run.llm.provider} {run.llm.model}, prompt {run.llm.prompt_version}{" "}
              <span className="mono muted">{short(run.llm.prompt_hash, 10)}</span>, temperature{" "}
              {run.llm.temperature}
            </>
          )}
        </dd>
        {strategyOf(run) === "llm" && (
          <>
            <dt>LLM usage</dt>
            <dd>{llmFacts(run.counts)}</dd>
          </>
        )}
        <dt>Found</dt>
        <dd>
          {FOUND_COUNTS.filter((c) => run.counts[c.key] != null)
            .map((c) => `${run.counts[c.key].toLocaleString()} ${c.label}`)
            .join(" · ") || "—"}
        </dd>
        <dt>Problems</dt>
        <dd>
          {PROBLEM_STATUSES.filter((s) => run.counts[s])
            .map((s) => `${run.counts[s]} ${s.replace(/_/g, " ")}`)
            .join(" · ") || "None"}
        </dd>
        {run.note && (
          <>
            <dt>Note</dt>
            <dd>{run.note}</dd>
          </>
        )}
        <dt>Request</dt>
        <dd className="mono">{JSON.stringify(run.settings)}</dd>
      </dl>
      {run.error && <div className="crawler-error">{run.error}</div>}

      <EvaluatePanel context={context} run={run} />

      <div className="crawler-chips">
        {Object.entries(run.asset_counts.by_status).map(([s, n]) => (
          <button
            key={s}
            className={`crawler-chip ${status === s ? "active" : ""} ${statusClass(s)}`}
            onClick={() => setStatus(status === s ? "" : s)}
          >
            {s}: {n}
          </button>
        ))}
        {Object.entries(run.asset_counts.by_class).map(([c, n]) => (
          <button
            key={c}
            className={`crawler-chip ${kind === c ? "active" : ""}`}
            onClick={() => setKind(kind === c ? "" : c)}
          >
            {c}: {n}
          </button>
        ))}
        <input
          className="crawler-search"
          placeholder="Search asset ID or object key"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <table className="crawler-table">
        <thead>
          <tr>
            <th>Asset</th>
            <th>Class</th>
            <th>Type</th>
            <th>Status</th>
            <th className="num">Size</th>
            <th>Document date</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {assets.slice(0, 500).map((a) => (
            <tr key={a.asset_id}>
              <td className="mono" title={a.object_key ?? a.asset_id}>
                {short(a.asset_id, 13)}
              </td>
              <td>{a.ontology_class}</td>
              <td>{a.mime_type}</td>
              <td>
                <span className={`crawler-badge ${statusClass(a.status)}`}>{a.status}</span>
              </td>
              <td className="num">{(a.size_bytes / 1024).toFixed(1)} KB</td>
              <td className="nowrap">{a.semantic_timestamp ? a.semantic_timestamp.slice(0, 10) : "—"}</td>
              <td className="crawler-problem">{a.status_detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {assets.length > 500 && <p className="muted">Showing 500 of {assets.length}; filter to narrow.</p>}
    </section>
  );
}

// --- evaluation ------------------------------------------------------------------

function EvaluatePanel({ context, run }: CrawlerPageProps & { run: CrawlRunDetail }) {
  const { crawlerClient } = context;
  const suggested = datasetOf(run);
  const [dataset, setDataset] = useState(suggested);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<CrawlEvaluation | null>(null);

  useEffect(() => {
    setDataset(suggested);
    setResult(null);
    setProblem(null);
  }, [suggested, run.crawl_run_id]);

  const evaluate = async () => {
    setProblem(null);
    setResult(null);
    setBusy(true);
    try {
      setResult(await crawlerClient().evaluateCrawlRun!(run.crawl_run_id, dataset.trim()));
    } catch (err) {
      setProblem(
        err instanceof AuthorizationError
          ? "You don't have access to the ground truth for this dataset."
          : message(err, "Evaluation failed"),
      );
    } finally {
      setBusy(false);
    }
  };

  const latest = result ?? run.latest_evaluation;
  return (
    <div className="crawler-evaluate">
      <div className="crawler-evaluate-row">
        <label>
          Ground-truth dataset{" "}
          <input
            className="crawler-search"
            aria-label="Ground-truth dataset"
            value={dataset}
            placeholder="Helios-DS dataset ID"
            onChange={(e) => setDataset(e.target.value)}
          />
        </label>
        <button
          className="crawler-button crawler-button--primary"
          disabled={busy || !dataset.trim()}
          onClick={() => void evaluate()}
        >
          {busy ? "Evaluating…" : "Evaluate"}
        </button>
        <span className="muted">
          Runs as you; Ranger decides whether you may read the ground truth.
        </span>
      </div>
      {problem && <div className="crawler-error">{problem}</div>}
      {latest && (
        <div className={result ? "crawler-success" : "crawler-evaluate-latest"}>
          {result ? "Scored" : "Last scored"} {when(latest.evaluated_at)} by {latest.evaluator} (
          {latest.evaluator_mode}) against <span className="mono">{short(latest.dataset_id, 13)}</span>:{" "}
          <span className="crawler-score">{scoreLabel(latest.summary)}</span>
          {latest.error && <div className="crawler-problem">{latest.error}</div>}
        </div>
      )}
    </div>
  );
}

// --- scores ----------------------------------------------------------------------------

const SUMMARY_COLUMNS: { key: string; label: string; title: string }[] = [
  { key: "segments_coverage", label: "Segments", title: "Truth evidence and mentions inside a crawler segment" },
  { key: "mention_recall_direct", label: "Direct", title: "Recall of direct (identifier) mentions" },
  { key: "mention_recall_alias", label: "Alias", title: "Recall of alias mentions" },
  { key: "mention_recall_contextual", label: "Contextual", title: "Recall of contextual mentions" },
  { key: "mention_precision", label: "Precision", title: "Crawler mentions that correspond to a truth mention" },
  { key: "resolution_accuracy_alias", label: "Res. alias", title: "Resolution accuracy of alias mentions" },
  { key: "sameas_precision", label: "SameAs", title: "Precision of definite (SameAs) links" },
  { key: "claim_precision", label: "Claim P", title: "Claim precision" },
  { key: "claim_recall", label: "Claim R", title: "Claim recall" },
  { key: "evidence_agreement", label: "Evidence", title: "Matched claims with an agreeing evidence locator" },
  { key: "questions_pass_rate", label: "Questions", title: "Golden questions passing at the retrieval level" },
];

type Cells = Record<string, Record<string, number | null>>;

// Short headings for the detail tables; the tooltip says what is counted.
const METRIC_COLUMNS: Record<string, { label: string; title: string }> = {
  truth_total: { label: "Expected", title: "How many the ground truth has" },
  truth_found: { label: "Found", title: "How many of those the crawler found" },
  recall: { label: "Recall", title: "Found ÷ expected" },
  class_correct_rate: { label: "Right class", title: "Share of found mentions given the right ontology class" },
  crawler_total: { label: "Produced", title: "How many the crawler produced" },
  crawler_matched: { label: "Correct", title: "How many of those match the ground truth" },
  precision: { label: "Precision", title: "Correct ÷ produced" },
  found: { label: "Found", title: "Truth mentions the crawler found" },
  correct: { label: "Correct", title: "Linked to the right entity" },
  wrong: { label: "Wrong", title: "Linked to the wrong entity" },
  possibly_only: { label: "Possible", title: "Only a tentative (PossiblySameAs) link" },
  unresolved: { label: "No link", title: "Found but not linked to any entity" },
  accuracy: { label: "Accuracy", title: "Correct ÷ found" },
  links: { label: "Links", title: "Definite (SameAs) links made this way" },
  total: { label: "Total", title: "Golden questions of this kind" },
  passed: { label: "Passed", title: "Questions whose entities, documents, claims and evidence were all found" },
  pass_rate: { label: "Pass rate", title: "Passed ÷ total" },
  crawler_pairs: { label: "Produced", title: "Document pairs the crawler put in the same case" },
  truth_pairs: { label: "Expected", title: "Document pairs that belong to the same case" },
  pairs_correct: { label: "Correct", title: "Pairs the crawler grouped correctly" },
};

function MetricTable({ title, rows, columns }: { title: string; rows: Cells | undefined; columns: string[] }) {
  const entries = Object.entries(rows ?? {});
  if (entries.length === 0) return null;
  return (
    <section className="crawler-metric">
      <h3>{title}</h3>
      <table className="crawler-table crawler-metric__table">
        <thead>
          <tr>
            <th />
            {columns.map((c) => (
              <th key={c} className="num" title={METRIC_COLUMNS[c]?.title}>
                {METRIC_COLUMNS[c]?.label ?? c.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, cell]) => (
            <tr key={name}>
              <td>{name.replace(/:(?=\S)/, ": ")}</td>
              {columns.map((c) => (
                <td key={c} className="num">
                  {typeof cell[c] === "number" && !Number.isInteger(cell[c]) ? pct(cell[c]) : (cell[c] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function EvaluationDetail({ evaluation }: { evaluation: CrawlEvaluation }) {
  const m = evaluation.metrics as Record<string, Record<string, unknown>>;
  const mentions = (m.mentions ?? {}) as { by_class_tier?: Record<string, Cells> };
  const classTier: Cells = {};
  for (const [cls, tiers] of Object.entries(mentions.by_class_tier ?? {})) {
    for (const [tier, cell] of Object.entries(tiers)) classTier[`${cls} / ${tier}`] = cell;
  }
  const diagnostics = (m.diagnostics?.mentions ?? {}) as {
    unmatched_crawler_mentions?: { extractor: string; class: string; count: number }[];
    unfound_truth_surfaces?: { class: string; tier: string; surface: string; count: number }[];
  };
  if (evaluation.status !== "SUCCEEDED") {
    return <div className="crawler-error">{evaluation.error ?? "evaluation failed"}</div>;
  }
  return (
    <div className="crawler-evaluation-detail">
      <MetricTable title="Mentions by class and tier" rows={classTier} columns={["truth_total", "truth_found", "recall", "class_correct_rate"]} />
      <MetricTable title="Mention precision" rows={(m.mentions as { precision_by?: Cells })?.precision_by} columns={["crawler_total", "crawler_matched", "precision"]} />
      <MetricTable title="Resolution by tier" rows={(m.resolution as { by_tier?: Cells })?.by_tier} columns={["found", "correct", "wrong", "possibly_only", "unresolved", "accuracy"]} />
      <MetricTable title="Resolution by resolver" rows={(m.resolution as { by_resolver?: Cells })?.by_resolver} columns={["links", "correct", "accuracy"]} />
      <MetricTable title="Claims by predicate" rows={(m.claims as { by_predicate?: Cells })?.by_predicate} columns={["truth_total", "truth_found", "recall", "crawler_total", "crawler_matched", "precision"]} />
      <MetricTable title="Relationships by type" rows={(m.relationships as { by_type?: Cells })?.by_type} columns={["truth_total", "truth_found", "recall", "crawler_total", "crawler_matched", "precision"]} />
      <MetricTable title="Questions by kind" rows={(m.questions as { by_kind?: Cells })?.by_kind} columns={["total", "passed", "pass_rate"]} />
      <MetricTable title="Cases (pairwise)" rows={m.cases && !(m.cases as { skipped?: boolean }).skipped ? { pairs: m.cases as Record<string, number | null> } : undefined} columns={["crawler_pairs", "truth_pairs", "pairs_correct", "precision", "recall"]} />
      <div className="crawler-diagnostics">
        <section className="crawler-metric">
          <h3>Unmatched crawler mentions</h3>
          <ul>
            {(diagnostics.unmatched_crawler_mentions ?? []).map((d) => (
              <li key={`${d.extractor}/${d.class}`}>
                {d.extractor} / {d.class}: {d.count}
              </li>
            ))}
          </ul>
        </section>
        <section className="crawler-metric">
          <h3>Most common unfound truth mentions</h3>
          <ul>
            {(diagnostics.unfound_truth_surfaces ?? []).map((d) => (
              <li key={`${d.class}/${d.tier}/${d.surface}`}>
                <span className="mono">{d.surface}</span> ({d.class} / {d.tier}): {d.count}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

/** The LLM measures the scorecard records for an LLM crawl (metrics.run.llm). */
interface LlmMeasures {
  provider: string;
  model: string;
  prompt_version: string;
  documents: number;
  cached: number;
  failed: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number | null;
  ms_per_document: number | null;
  hallucinated_spans: number;
  hallucinated_span_rate: number | null;
}

function llmMeasures(evaluation: CrawlEvaluation): LlmMeasures | null {
  const run = (evaluation.metrics?.run ?? null) as { llm?: LlmMeasures | null } | null;
  return run?.llm ?? null;
}

const LLM_COLUMNS: { label: string; title: string; value: (llm: LlmMeasures) => string }[] = [
  {
    label: "Not in doc.",
    title:
      "Share of the entities and claims the model returned whose quoted text was not in the document. These were dropped, not stored.",
    // A percentage with one decimal: the page's two-decimal ratios would show 4.5% as 0.05.
    value: (llm) =>
      llm.hallucinated_span_rate == null ? "—" : `${(llm.hallucinated_span_rate * 100).toFixed(1)}%`,
  },
  {
    label: "Tokens",
    title: "Tokens sent to and received from the model in this crawl. Documents answered from the cache add none.",
    value: (llm) => (llm.tokens_in + llm.tokens_out).toLocaleString(),
  },
  {
    label: "Sec / doc",
    title: "Average model response time per document, for documents not answered from the cache",
    value: (llm) => (llm.ms_per_document == null ? "—" : (llm.ms_per_document / 1000).toFixed(1)),
  },
  {
    label: "Cost",
    title: "Model cost of this crawl, when token prices are set in the crawler settings (llm section)",
    value: (llm) => (llm.cost_usd == null ? "—" : `$${llm.cost_usd.toFixed(2)}`),
  },
];

function ScoresTab({ context }: CrawlerPageProps) {
  const { crawlerClient } = context;
  const [all, setAll] = useState<CrawlEvaluation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dataset, setDataset] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setAll(await crawlerClient().crawlerEvaluations!());
    } catch (err) {
      setError(message(err, "Failed to load evaluations"));
    }
  }, [crawlerClient]);

  useEffect(() => {
    void load();
  }, [load]);

  const datasets = useMemo(() => Array.from(new Set((all ?? []).map((e) => e.dataset_id))).sort(), [all]);
  const selected = dataset || datasets[0] || "";
  const shown = (all ?? []).filter((e) => e.dataset_id === selected);
  // The LLM columns appear only when an LLM crawl has been scored on this dataset.
  const llmColumns = shown.some((e) => llmMeasures(e)) ? LLM_COLUMNS : [];

  if (error) return <ErrorState title="Failed to load evaluations" message={error} />;
  if (!all) return <LoadingState />;

  return (
    <div className="crawler-runs">
      <div className="crawler-toolbar">
        <label>
          Dataset{" "}
          <select value={selected} onChange={(e) => setDataset(e.target.value)}>
            {datasets.map((d) => (
              <option key={d} value={d}>
                {short(d, 13)}
              </option>
            ))}
          </select>
        </label>
        <button className="crawler-button" onClick={() => void load()}>
          Refresh
        </button>
        <span className="muted">One row per crawl run: its latest evaluation against this dataset.</span>
      </div>
      {shown.length === 0 ? (
        <div className="crawler-empty">No evaluations yet. Open a run and click Evaluate.</div>
      ) : (
        <table className="crawler-table crawler-scores">
          <thead>
            <tr>
              <th>Run started</th>
              <th>Strategy</th>
              <th>Crawler</th>
              <th>Settings</th>
              <th>Status</th>
              {SUMMARY_COLUMNS.map((c) => (
                <th key={c.key} className="num" title={c.title}>
                  {c.label}
                </th>
              ))}
              {llmColumns.map((c) => (
                <th key={c.label} className="num crawler-scores__llm" title={c.title}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <Fragment key={e.evaluation_id}>
                <tr
                  className={`clickable ${open === e.evaluation_id ? "selected" : ""}`}
                  onClick={() => setOpen(open === e.evaluation_id ? null : e.evaluation_id)}
                >
                  <td className="nowrap" title={e.crawl_run_id}>
                    {when(e.run?.started_at)}
                  </td>
                  <td>{e.strategy}</td>
                  <td>{e.run?.crawler_version ?? "—"}</td>
                  <td>{e.run?.settings_version ?? "defaults"}</td>
                  <td>
                    <span className={`crawler-badge ${statusClass(e.status)}`}>{e.status}</span>
                  </td>
                  {SUMMARY_COLUMNS.map((c) => (
                    <td key={c.key} className="num">
                      {pct(e.summary[c.key])}
                    </td>
                  ))}
                  {llmColumns.map((c) => {
                    const llm = llmMeasures(e);
                    return (
                      <td key={c.label} className="num crawler-scores__llm">
                        {llm ? c.value(llm) : "—"}
                      </td>
                    );
                  })}
                </tr>
                {open === e.evaluation_id && (
                  <tr className="crawler-scores-detail">
                    <td colSpan={5 + SUMMARY_COLUMNS.length + llmColumns.length}>
                      <div className="muted">
                        Evaluated {when(e.evaluated_at)} by {e.evaluator} ({e.evaluator_mode}), harness{" "}
                        {e.harness_version}, ontology {e.ontology_version}.
                      </div>
                      <LlmScoreNote evaluation={e} />
                      <EvaluationDetail evaluation={e} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function LlmScoreNote({ evaluation }: { evaluation: CrawlEvaluation }) {
  const llm = llmMeasures(evaluation);
  if (!llm) return null;
  return (
    <div className="crawler-llm-note">
      <strong>LLM crawl:</strong> {llm.provider} {llm.model}, prompt {llm.prompt_version}.{" "}
      {llm.documents.toLocaleString()} documents read ({llm.cached.toLocaleString()} from cache
      {llm.failed ? `, ${llm.failed} failed` : ""}); {llm.tokens_in.toLocaleString()} tokens in,{" "}
      {llm.tokens_out.toLocaleString()} out; {llm.hallucinated_spans.toLocaleString()} quotes not
      in the documents were dropped.
      <br />
      This crawl links only on exact keys the model quoted. A return is identified by its RMA or
      case number, which is not a warehouse key, so links and claims involving returns score as
      unmatched; that lowers link precision, claims and golden questions.
    </div>
  );
}

// --- settings ----------------------------------------------------------------------

function SettingsTab({ context }: CrawlerPageProps) {
  const { crawlerClient } = context;
  const [state, setState] = useState<CrawlerSettingsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // label of what's loaded
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CrawlerSettingsSaveResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await crawlerClient().crawlerSettings!();
      setState(data);
      return data;
    } catch (err) {
      setError(message(err, "Failed to load crawler settings"));
      return null;
    }
  }, [crawlerClient]);

  useEffect(() => {
    void load().then((data) => {
      if (data) {
        setText(JSON.stringify(data.settings, null, 2));
        setEditing(data.using_defaults ? "built-in defaults" : `version ${data.active_version} (active)`);
      }
    });
  }, [load]);

  const loadInto = async (label: string, fetcher: () => Promise<{ settings: Record<string, unknown> }>) => {
    setProblem(null);
    setResult(null);
    try {
      const data = await fetcher();
      setText(JSON.stringify(data.settings, null, 2));
      setEditing(label);
    } catch (err) {
      setProblem(message(err, "Failed to load settings"));
    }
  };

  const save = async () => {
    setProblem(null);
    setResult(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      setProblem(`Not valid JSON: ${message(err, "parse error")}`);
      return;
    }
    setBusy(true);
    try {
      const saved = await crawlerClient().saveCrawlerSettings!(parsed, note);
      setResult(saved);
      setNote("");
      await load();
    } catch (err) {
      setProblem(message(err, "Saving failed"));
    } finally {
      setBusy(false);
    }
  };

  const activate = async (version: number) => {
    setProblem(null);
    setBusy(true);
    try {
      await crawlerClient().activateCrawlerSettings!(version);
      await load();
      setResult(null);
    } catch (err) {
      setProblem(message(err, "Activation failed"));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState title="Failed to load crawler settings" message={error} />;
  if (!state) return <LoadingState />;

  return (
    <div className="crawler-settings">
      <div className="crawler-settings-status">
        Crawls use{" "}
        <strong>{state.using_defaults ? "the built-in defaults" : `version ${state.active_version}`}</strong>{" "}
        <span className="mono muted">{short(state.content_hash, 12)}</span>. Saving creates a new,
        immutable version; activating it applies it from the next crawl.
      </div>

      <div className="crawler-settings-grid">
        <section>
          <h2>Versions</h2>
          {state.versions.length === 0 ? (
            <p className="muted">No saved versions yet.</p>
          ) : (
            <table className="crawler-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Saved</th>
                  <th>Note</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {state.versions.map((v) => (
                  <tr key={v.version} className={v.active ? "selected" : ""}>
                    <td>
                      {v.version} {v.active && <span className="crawler-badge crawler-badge--ok">active</span>}
                    </td>
                    <td className="nowrap">
                      {when(v.created_at)}
                      <div className="muted">{v.created_by}</div>
                    </td>
                    <td>{v.note || <span className="muted">—</span>}</td>
                    <td className="nowrap">
                      <button
                        className="crawler-button"
                        onClick={() =>
                          void loadInto(`version ${v.version}`, () =>
                            crawlerClient().crawlerSettingsVersion!(v.version),
                          )
                        }
                      >
                        Edit
                      </button>{" "}
                      {!v.active && (
                        <button className="crawler-button" disabled={busy} onClick={() => void activate(v.version)}>
                          Activate
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <button
            className="crawler-button"
            onClick={() => void loadInto("built-in defaults", () => crawlerClient().crawlerSettingsDefaults!())}
          >
            Start from the built-in defaults
          </button>
          <details className="crawler-help">
            <summary>What the sections mean</summary>
            <ul>
              <li><code>analyzers</code>: which asset types are read (pdf, email, chat) and their options.</li>
              <li><code>patterns</code>: regular expressions for identifiers; <code>key</code> patterns are looked up in the listed TPC-DS columns.</li>
              <li><code>pdf_labels</code>: PDF field labels and what kind of value follows them.</li>
              <li><code>contextual</code>: phrases such as “the item”, per ontology class.</li>
              <li><code>cases</code>: which identifiers link documents into one case.</li>
              <li><code>claims</code>: cue words per claim predicate, plus negation and hedge words.</li>
            </ul>
            <p>
              How classes are identified (keys, aliases, alias templates, thresholds) is part of the
              ontology mapping, not these settings. See <code>docs/crawler-analysis.md</code>.
            </p>
          </details>
        </section>

        <section className="crawler-editor">
          <h2>
            Edit <span className="muted">(loaded: {editing ?? "—"})</span>
          </h2>
          <textarea
            className="crawler-json"
            spellCheck={false}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Crawler settings JSON"
          />
          <div className="crawler-editor-actions">
            <button
              className="crawler-button"
              onClick={() => {
                try {
                  setText(JSON.stringify(JSON.parse(text), null, 2));
                  setProblem(null);
                } catch (err) {
                  setProblem(`Not valid JSON: ${message(err, "parse error")}`);
                }
              }}
            >
              Format
            </button>
            <input
              className="crawler-note"
              placeholder="Note: what changed and why"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="crawler-button crawler-button--primary" disabled={busy} onClick={() => void save()}>
              Validate and save
            </button>
          </div>
          {problem && <pre className="crawler-error">{problem}</pre>}
          {result && (
            <div className="crawler-success">
              {result.created ? `Saved as version ${result.version}.` : `Identical to version ${result.version}; nothing new saved.`}
              {result.warnings.map((w) => (
                <div key={w} className="muted">Warning: {w}</div>
              ))}
              {!result.active && (
                <button className="crawler-button" disabled={busy} onClick={() => void activate(result.version)}>
                  Activate version {result.version}
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
