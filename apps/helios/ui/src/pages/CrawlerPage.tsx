import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type {
  CrawlRunDetail,
  CrawlRunSummary,
  CrawlerSettingsSaveResult,
  CrawlerSettingsState,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import "./CrawlerPage.css";

interface CrawlerPageProps {
  context: ApplicationContextState;
}

type Tab = "runs" | "settings";

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

const short = (value: string | null | undefined, n = 12) =>
  value ? (value.length > n ? `${value.slice(0, n)}…` : value) : "—";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

function duration(run: CrawlRunSummary): string {
  if (!run.finished_at) return run.status === "RUNNING" ? "running" : "—";
  const seconds = (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000;
  return seconds < 120 ? `${seconds.toFixed(0)} s` : `${(seconds / 60).toFixed(1)} min`;
}

// Asset statuses that mean the asset was usable in the run.
const GOOD_STATUSES = new Set(["fetched", "carried_forward", "analyzed"]);

function problemCount(counts: Record<string, number>): number {
  return Object.entries(counts)
    .filter(([status]) => status !== "listed" && status !== "segments" && !GOOD_STATUSES.has(status))
    .reduce((total, [, n]) => total + n, 0);
}

function statusClass(status: string): string {
  if (status === "SUCCEEDED" || GOOD_STATUSES.has(status)) return "crawler-badge--ok";
  if (status === "RUNNING") return "crawler-badge--running";
  return "crawler-badge--problem";
}

export default function CrawlerPage({ context }: CrawlerPageProps) {
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "settings" ? "settings" : "runs";
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
          What the crawler read, what it found, and the settings it runs with. Crawls run as a
          Workbench Job (<code>python -m apps.helios.crawler crawl --dataset &lt;id&gt;</code>).
        </p>
        <div className="crawler-tabs" role="tablist">
          {(["runs", "settings"] as Tab[]).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`crawler-tab ${tab === t ? "active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "runs" ? "Runs" : "Settings"}
            </button>
          ))}
        </div>
      </div>
      {tab === "runs" ? <RunsTab context={context} /> : <SettingsTab context={context} />}
    </div>
  );
}

// --- runs --------------------------------------------------------------------------

function RunsTab({ context }: CrawlerPageProps) {
  const { crawlerClient } = context;
  const [runs, setRuns] = useState<CrawlRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState("");
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

  const sources = useMemo(() => Array.from(new Set((runs ?? []).map((r) => r.source))).sort(), [runs]);
  const shown = (runs ?? []).filter((r) => !source || r.source === source);

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
        <button className="crawler-button" onClick={() => void load()}>
          Refresh
        </button>
      </div>

      {shown.length === 0 ? (
        <div className="crawler-empty">No crawl runs yet. Run the crawler Job to create one.</div>
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
                <td className="mono">{short(run.source, 13)}</td>
                <td>
                  <span className={`crawler-badge ${statusClass(run.status)}`}>{run.status}</span>
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
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && <RunDetail context={context} crawlRunId={selected} />}
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
        <dt>Request</dt>
        <dd className="mono">{JSON.stringify(run.settings)}</dd>
      </dl>
      {run.error && <div className="crawler-error">{run.error}</div>}

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
