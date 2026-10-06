import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  DataSourceInput,
  DataSourceTestResult,
  DataSourceType,
  DataSourceView,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import "./CrawlerPage.css";
import "./DataSourcesPage.css";

interface DataSourcesPageProps {
  context: ApplicationContextState;
}

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");
const list = (value: string) =>
  value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
const joined = (value: unknown) => (Array.isArray(value) ? value.join(", ") : "");

/** One-line description of what a source crawls. */
export function scopeSummary(source: Pick<DataSourceView, "connector" | "scope">): string {
  const s = source.scope as Record<string, unknown>;
  if (source.connector === "helios_ds") return `Helios-DS dataset ${String(s.dataset_id ?? "")}`;
  if (source.connector === "object_store") return `s3://${String(s.bucket ?? "")}/${String(s.prefix ?? "")}`;
  if (source.connector === "table_rows")
    return `${String(s.table ?? "")}: ${joined(s.text_columns)} by ${joined(s.key_columns)}`;
  return "used by semantic models";
}

// --- the form: plain fields per connector type, turned into the API's scope --------

type Form = Record<string, string | boolean>;

const DEFAULT_CONNECTION: Record<string, string> = {
  helios_ds: "S3 Object Store",
  object_store: "S3 Object Store",
  table_rows: "impala",
};

function formFrom(source?: DataSourceView, connector = "helios_ds"): Form {
  const s = (source?.scope ?? {}) as Record<string, unknown>;
  const kind = source?.connector ?? connector;
  return {
    name: source?.name ?? "",
    description: source?.description ?? "",
    connector: kind,
    connection_ref: source?.connection_ref ?? DEFAULT_CONNECTION[kind] ?? "",
    enabled: source?.crawl?.enabled ?? true,
    settings_version: source?.crawl?.settings_version ? String(source.crawl.settings_version) : "",
    dataset_id: String(s.dataset_id ?? ""),
    bucket: String(s.bucket ?? ""),
    prefix: String(s.prefix ?? ""),
    include: joined(s.include) || "*",
    exclude: joined(s.exclude),
    max_bytes: String(s.max_bytes ?? 50000000),
    max_objects: String(s.max_objects ?? 100000),
    table: String(s.table ?? ""),
    key_columns: joined(s.key_columns),
    text_columns: joined(s.text_columns),
    timestamp_column: String(s.timestamp_column ?? ""),
    filters: Object.entries((s.filters ?? {}) as Record<string, unknown>)
      .map(([k, v]) => `${k}=${String(v)}`)
      .join(", "),
    max_rows: String(s.max_rows ?? 100000),
  };
}

/** What is still missing before a data source can be saved, in the form's words. */
export function missingFields(input: DataSourceInput): string[] {
  const scope = input.scope as Record<string, unknown>;
  const blank = (value: unknown) => !String(value ?? "").trim();
  const missing: string[] = [];
  if (blank(input.name)) missing.push("Name");
  if (blank(input.connection_ref)) missing.push("Connection");
  if (input.connector === "helios_ds" && blank(scope.dataset_id)) missing.push("Dataset ID");
  if (input.connector === "object_store" && blank(scope.bucket)) missing.push("Bucket");
  if (input.connector === "table_rows" && blank(scope.table)) missing.push("Table");
  return missing;
}

export function inputFrom(form: Form): DataSourceInput {
  const kind = String(form.connector);
  const number = (key: string) => Number(form[key]);
  let scope: Record<string, unknown> = {};
  if (kind === "helios_ds") scope = { dataset_id: String(form.dataset_id).trim() };
  if (kind === "object_store")
    scope = {
      bucket: String(form.bucket).trim(),
      prefix: String(form.prefix).trim(),
      include: list(String(form.include)).length ? list(String(form.include)) : ["*"],
      exclude: list(String(form.exclude)),
      max_bytes: number("max_bytes"),
      max_objects: number("max_objects"),
    };
  if (kind === "table_rows") {
    const filters: Record<string, string> = {};
    for (const pair of list(String(form.filters))) {
      const [k, ...rest] = pair.split("=");
      if (k && rest.length) filters[k.trim()] = rest.join("=").trim();
    }
    scope = {
      table: String(form.table).trim(),
      key_columns: list(String(form.key_columns)),
      text_columns: list(String(form.text_columns)),
      timestamp_column: String(form.timestamp_column).trim() || null,
      filters,
      max_rows: number("max_rows"),
    };
  }
  return {
    name: String(form.name).trim(),
    description: String(form.description),
    connector: kind,
    connection_ref: String(form.connection_ref).trim(),
    scope,
    crawl: {
      enabled: Boolean(form.enabled),
      settings_version: form.settings_version ? Number(form.settings_version) : null,
      schedule: "manual",
    },
  };
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="ds-field">
      <span className="ds-field__label">{label}</span>
      {children}
      {help && <span className="ds-field__help">{help}</span>}
    </label>
  );
}

function SourceForm({
  types,
  initial,
  onCancel,
  onSave,
}: {
  types: DataSourceType[];
  initial?: DataSourceView;
  onCancel: () => void;
  onSave: (input: DataSourceInput) => Promise<void>;
}) {
  const [form, setForm] = useState<Form>(() => formFrom(initial));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value }));
  const kind = String(form.connector);
  const type = types.find((t) => t.connector === kind);

  const submit = async () => {
    const input = inputFrom(form);
    const missing = missingFields(input);
    if (missing.length) {
      setError(`Fill in ${missing.join(", ")} before saving.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSave(input);
    } catch (err) {
      setError(message(err, "Saving failed"));
    } finally {
      setBusy(false);
    }
  };

  const input = (key: string, placeholder = "") => (
    <input value={String(form[key] ?? "")} onChange={set(key)} placeholder={placeholder} />
  );

  return (
    <section className="ds-form" aria-label={initial ? "Edit data source" : "Add a data source"}>
      <h2>{initial ? `Edit ${initial.name}` : "Add a data source"}</h2>
      <div className="ds-grid">
        <Field label="Type">
          <select
            value={kind}
            disabled={Boolean(initial)}
            onChange={(e) =>
              setForm((f) => ({ ...f, connector: e.target.value, connection_ref: DEFAULT_CONNECTION[e.target.value] ?? "" }))
            }
          >
            {types.map((t) => (
              <option key={t.connector} value={t.connector}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name">{input("name", "e.g. Support tickets")}</Field>
        <Field label="Connection" help={type?.connection_help}>
          {input("connection_ref")}
        </Field>
        <Field label="Description">{input("description")}</Field>

        {kind === "helios_ds" && <Field label="Dataset ID">{input("dataset_id", "a READY Helios-DS dataset")}</Field>}

        {kind === "object_store" && (
          <>
            <Field label="Bucket">{input("bucket")}</Field>
            <Field label="Prefix" help="Only objects under this path">{input("prefix", "e.g. contracts/2026/")}</Field>
            <Field label="Include" help="Comma-separated patterns, relative to the prefix">{input("include", "*.pdf, *.eml")}</Field>
            <Field label="Exclude">{input("exclude", "drafts/*")}</Field>
            <Field label="Largest object (bytes)">{input("max_bytes")}</Field>
            <Field label="Most objects">{input("max_objects")}</Field>
          </>
        )}

        {kind === "table_rows" && (
          <>
            <Field label="Table" help="database.table">{input("table", "support.tickets")}</Field>
            <Field label="Key columns" help="Identify a row">{input("key_columns", "ticket_id")}</Field>
            <Field label="Text columns" help="Each becomes one segment">{input("text_columns", "subject, body")}</Field>
            <Field label="Date column">{input("timestamp_column", "opened_at")}</Field>
            <Field label="Filters" help="column=value, comma-separated">{input("filters", "region=west")}</Field>
            <Field label="Most rows">{input("max_rows")}</Field>
          </>
        )}

        <Field label="Crawler settings version" help="Empty: the active version">
          {input("settings_version")}
        </Field>
        <label className="ds-check">
          <input type="checkbox" checked={Boolean(form.enabled)} onChange={set("enabled")} /> Crawling enabled
        </label>
      </div>
      {error && <pre className="crawler-error">{error}</pre>}
      <div className="ds-actions">
        <button className="button button--secondary ds-button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="button button--primary ds-button" onClick={() => void submit()} disabled={busy}>
          {initial ? "Save changes" : "Add data source"}
        </button>
      </div>
    </section>
  );
}

// --- the page ----------------------------------------------------------------------

export default function DataSourcesPage({ context }: DataSourcesPageProps) {
  const { crawlerClient, selectedOrganizationId } = context;
  const [types, setTypes] = useState<DataSourceType[]>([]);
  const [sources, setSources] = useState<DataSourceView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<DataSourceView | "new" | null>(null);
  const [tests, setTests] = useState<Record<string, DataSourceTestResult | "running">>({});
  const [notice, setNotice] = useState<string | null>(null);

  const api = crawlerClient;
  const load = useCallback(async () => {
    if (!selectedOrganizationId) return;
    setError(null);
    try {
      const [t, s] = await Promise.all([
        api().dataSourceTypes!(),
        api().dataSources!(selectedOrganizationId),
      ]);
      setTypes(t);
      setSources(s);
    } catch (err) {
      setError(message(err, "Failed to load data sources"));
    }
  }, [api, selectedOrganizationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const labels = useMemo(() => Object.fromEntries(types.map((t) => [t.connector, t.label])), [types]);

  if (!selectedOrganizationId) {
    return (
      <div className="ds-page">
        <div className="crawler-empty">Select an organization to see its data sources.</div>
      </div>
    );
  }
  if (error) return <ErrorState title="Failed to load data sources" message={error} />;
  if (!sources) return <LoadingState />;

  const save = async (input: DataSourceInput) => {
    if (editing === "new") {
      const created = await api().createDataSource!(selectedOrganizationId, input);
      setNotice(`Added ${created.name}. Crawl it with: python -m apps.helios.crawler crawl --source ${created.id}`);
    } else if (editing) {
      await api().updateDataSource!(selectedOrganizationId, editing.id, input);
      setNotice(`Saved ${input.name}.`);
    }
    setEditing(null);
    await load();
  };

  const remove = async (source: DataSourceView) => {
    if (!window.confirm(`Delete the data source "${source.name}"? Its crawl history stays in helios_index.`)) return;
    try {
      await api().deleteDataSource!(selectedOrganizationId, source.id);
      setNotice(`Deleted ${source.name}.`);
      await load();
    } catch (err) {
      setNotice(message(err, "Deleting failed"));
    }
  };

  const test = async (source: DataSourceView) => {
    setTests((t) => ({ ...t, [source.id]: "running" }));
    try {
      const result = await api().testDataSource!(selectedOrganizationId, source.id);
      setTests((t) => ({ ...t, [source.id]: result }));
    } catch (err) {
      setTests((t) => ({ ...t, [source.id]: { ok: false, detail: message(err, "Test failed"), sample: [] } }));
    }
  };

  return (
    <div className="ds-page">
      <div className="crawler-header">
        <div className="ds-title-row">
          <h1>Data Sources</h1>
          {editing === null && (
            <button className="button button--primary ds-button" onClick={() => setEditing("new")}>
              Add a data source
            </button>
          )}
        </div>
        <p className="crawler-subtitle">
          Where Helios's data lives. Crawlable sources (documents, object stores, table rows) are read
          by the crawler; warehouse sources are used by semantic models. Credentials stay in Workbench
          data connections: a source only names one.
        </p>
      </div>

      <div className="ds-body">
        {notice && <div className="crawler-success">{notice}</div>}
        {editing !== null && (
          <SourceForm
            types={types}
            initial={editing === "new" ? undefined : editing}
            onCancel={() => setEditing(null)}
            onSave={save}
          />
        )}

        {sources.length === 0 ? (
          <div className="crawler-empty">No data sources yet. Add one to start crawling.</div>
        ) : (
          <table className="crawler-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Scope</th>
                <th>Connection</th>
                <th>Last crawl</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const result = tests[s.id];
                return (
                  <tr key={s.id}>
                    <td>
                      <strong>{s.name}</strong>
                      {s.description && <div className="muted">{s.description}</div>}
                      <div className="mono muted">{s.id}</div>
                    </td>
                    <td>
                      {labels[s.connector] ?? s.connector}
                      {s.crawlable && s.crawl.enabled === false && <div className="muted">crawling disabled</div>}
                    </td>
                    <td className="ds-scope">{scopeSummary(s)}</td>
                    <td className="mono">{s.connection_ref}</td>
                    <td>
                      {s.last_crawl ? (
                        <>
                          <span className={`crawler-badge ${s.last_crawl.status === "SUCCEEDED" ? "crawler-badge--ok" : "crawler-badge--problem"}`}>
                            {s.last_crawl.status}
                          </span>
                          <div className="muted">{when(s.last_crawl.started_at)}</div>
                          <div className="muted">
                            {s.last_crawl.counts.listed ?? 0} listed · {s.last_crawl.counts.segments ?? 0} segments
                          </div>
                        </>
                      ) : (
                        <span className="muted">{s.crawlable ? "never crawled" : "—"}</span>
                      )}
                    </td>
                    <td className="ds-row-actions">
                      {s.crawlable ? (
                        <>
                          <button className="button button--secondary ds-button" onClick={() => setEditing(s)}>
                            Edit
                          </button>
                          <button className="button button--secondary ds-button" disabled={result === "running"} onClick={() => void test(s)}>
                            {result === "running" ? "Testing…" : "Test connection"}
                          </button>
                          <button className="button button--secondary ds-button" onClick={() => void remove(s)}>
                            Delete
                          </button>
                          {result && result !== "running" && (
                            <div className={result.ok ? "crawler-success" : "crawler-error"}>
                              {result.detail}
                              {result.sample.length > 0 && (
                                <ul className="ds-sample">
                                  {result.sample.slice(0, 5).map((a) => (
                                    <li key={a.asset_id} className="mono">
                                      {a.asset_id} <span className="muted">{a.mime_type}</span>
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          )}
                          <div className="muted ds-command">
                            Crawl: <code>python -m apps.helios.crawler crawl --source {s.id}</code>
                          </div>
                        </>
                      ) : (
                        <span className="muted">managed with its models</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
