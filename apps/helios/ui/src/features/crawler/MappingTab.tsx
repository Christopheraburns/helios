/** The Mapping tab: versions of the source mapping, an editor (form or JSON),
 * a check against the semantic model and ontology, and questions put to the
 * warehouse before anything is saved. */
import { useCallback, useEffect, useMemo, useState } from "react";

import type { HeliosApi, MappingCheck, MappingList, MappingModel, MappingProbe, MappingVersion } from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import MappingForm, { type Mapping, type ProbeTarget } from "./MappingForm";

type Api = Pick<
  HeliosApi,
  | "mappings"
  | "shippedMappings"
  | "mappingVersion"
  | "mappingModels"
  | "mappingModel"
  | "validateMapping"
  | "saveMapping"
  | "activateMapping"
  | "probeMapping"
>;

const EMPTY: Mapping = { model: "", ontology_version: "", database: "", entities: [], relationships: [], concepts: [], anchors: [] };

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function when(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** What the warehouse said about one class's table or one case record's query. */
export function ProbePanel({ title, probe, problem, busy }: { title: string; probe: MappingProbe | null; problem: string | null; busy: boolean }) {
  if (!title) {
    return (
      <aside className="try-panel" aria-label="Warehouse check">
        <h3>Warehouse check</h3>
        <p className="muted">
          Press <strong>Check in the warehouse</strong> on a class to see whether its key is unique
          and what its identifiers look like, or <strong>Run its query</strong> on a case record.
          The queries run as you; nothing is saved.
        </p>
      </aside>
    );
  }
  return (
    <aside className="try-panel" aria-label="Warehouse check">
      <h3>{title}</h3>
      {busy && <p className="muted">Asking the warehouse…</p>}
      {problem && (
        <pre className="crawler-error" role="alert">
          {problem}
        </pre>
      )}
      {probe && !busy && (
        <>
          {probe.kind === "entity" && (
            <p className="try-summary">
              <strong>{(probe.rows ?? 0).toLocaleString()}</strong> rows in <span className="mono">{probe.table}</span>,{" "}
              <strong>{(probe.distinct_keys ?? 0).toLocaleString()}</strong> different keys.{" "}
              {probe.key_is_unique ? "The key is unique." : ""}
            </p>
          )}
          {probe.kind === "anchor" && probe.sample.length > 0 && <p className="try-summary">The query runs. First rows:</p>}
          {probe.findings.map((finding) => (
            <p className="mapping-warning" role="alert" key={finding}>
              {finding}
            </p>
          ))}
          {probe.sample.length > 0 && (
            <div className="mapping-sample">
              <table className="crawler-table">
                <thead>
                  <tr>
                    {probe.columns.map((column, index) => (
                      <th key={`${column}-${index}`} className="mono">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {probe.sample.map((row, index) => (
                    <tr key={index}>
                      {row.map((cell, at) => (
                        <td key={at}>{cell === null ? <span className="muted">null</span> : String(cell)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details className="crawler-help">
            <summary>The SQL that ran</summary>
            {probe.sql.map((sql) => (
              <pre className="mapping-sql" key={sql}>
                {sql}
              </pre>
            ))}
          </details>
        </>
      )}
    </aside>
  );
}

export default function MappingTab({ api }: { api: () => Api }) {
  const [list, setList] = useState<MappingList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shipped, setShipped] = useState<Mapping[]>([]);
  const [options, setOptions] = useState<{ models: string[]; classes: string[] | null }>({ models: [], classes: null });
  const [model, setModel] = useState<MappingModel | null>(null);
  const [text, setText] = useState(JSON.stringify(EMPTY, null, 2));
  const [editing, setEditing] = useState("a new mapping");
  const [formMode, setFormMode] = useState(true);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [check, setCheck] = useState<MappingCheck | null>(null);
  const [saved, setSaved] = useState<(MappingVersion & { created: boolean; not_checked: string[] }) | null>(null);
  const [probing, setProbing] = useState<{ title: string; probe: MappingProbe | null; problem: string | null; busy: boolean }>({
    title: "",
    probe: null,
    problem: null,
    busy: false,
  });

  const draft = useMemo<Mapping | null>(() => {
    try {
      const parsed: unknown = JSON.parse(text);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Mapping) : null;
    } catch {
      return null;
    }
  }, [text]);
  const modelName: string = typeof draft?.model === "string" ? draft.model : "";

  const load = useCallback(async () => {
    setError(null);
    try {
      const found = await api().mappings!();
      setList(found);
      return found;
    } catch (err) {
      setError(message(err, "Failed to load mappings"));
      return null;
    }
  }, [api]);

  const edit = useCallback(
    async (version: number, label: string) => {
      setProblem(null);
      setCheck(null);
      setSaved(null);
      try {
        const found = await api().mappingVersion!(version);
        setText(JSON.stringify(found.mapping, null, 2));
        setEditing(label);
      } catch (err) {
        setProblem(message(err, "Failed to load the mapping"));
      }
    },
    [api],
  );

  useEffect(() => {
    let active = true;
    void (async () => {
      const found = await load();
      // Start on what crawls use today, when there is one.
      const first = found?.versions.find((v) => v.is_active);
      if (active && first) await edit(first.version, `version ${first.version} (active)`);
      try {
        const [examples, picks] = await Promise.all([api().shippedMappings!(), api().mappingModels!()]);
        if (active) {
          setShipped(examples);
          setOptions(picks);
        }
      } catch {
        // Examples and pickers are conveniences; the editor works without them.
      }
    })();
    return () => {
      active = false;
    };
  }, [api, load, edit]);

  useEffect(() => {
    let active = true;
    setModel(null);
    if (!modelName || !options.models.includes(modelName)) return;
    void (async () => {
      try {
        const found = await api().mappingModel!(modelName);
        if (active) setModel(found);
      } catch {
        if (active) setModel(null);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, modelName, options.models]);

  const parsed = (): Mapping | null => {
    if (draft === null) setProblem("Not valid JSON: fix it in the JSON view, or load a version.");
    return draft;
  };

  const validate = async () => {
    const mapping = parsed();
    if (!mapping) return;
    setProblem(null);
    setSaved(null);
    setBusy(true);
    try {
      setCheck(await api().validateMapping!(mapping));
    } catch (err) {
      setProblem(message(err, "The check failed"));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const mapping = parsed();
    if (!mapping) return;
    setProblem(null);
    setCheck(null);
    setSaved(null);
    setBusy(true);
    try {
      const result = await api().saveMapping!(mapping, note);
      setSaved(result);
      setNote("");
      setEditing(`version ${result.version}`);
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
      await api().activateMapping!(version);
      setSaved(null);
      await load();
    } catch (err) {
      setProblem(message(err, "Activation failed"));
    } finally {
      setBusy(false);
    }
  };

  const probe = async (target: ProbeTarget, title: string) => {
    if (!draft) return;
    setProbing({ title, probe: null, problem: null, busy: true });
    try {
      const found = await api().probeMapping!(draft, target);
      setProbing({ title, probe: found, problem: null, busy: false });
    } catch (err) {
      setProbing({ title, probe: null, problem: message(err, "The warehouse could not be asked"), busy: false });
    }
  };

  if (error) return <ErrorState title="Failed to load mappings" message={error} />;
  if (!list) return <LoadingState />;

  const activeModels = Object.entries(list.active);
  return (
    <div className="crawler-settings">
      <div className="crawler-settings-status">
        The mapping says where each ontology class is in the warehouse.{" "}
        {activeModels.length === 0 ? (
          <strong>No mapping is active: crawls cannot look anything up in the warehouse.</strong>
        ) : (
          <>
            Crawls use{" "}
            {activeModels.map(([name, version], index) => (
              <span key={name}>
                {index > 0 && ", "}
                <strong>version {version}</strong> for <span className="mono">{name}</span>
              </span>
            ))}
            .
          </>
        )}{" "}
        Saving creates a new, immutable version; activating it applies it from the next crawl.
      </div>

      <div className="crawler-settings-stack">
        <details className="crawler-versions" open={!formMode || list.versions.length === 0}>
          <summary>
            Versions and examples <span className="muted">({list.versions.length} saved)</span>
          </summary>
          {list.versions.length === 0 ? (
            <p className="muted">No saved versions yet.</p>
          ) : (
            <table className="crawler-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Semantic model</th>
                  <th>Saved</th>
                  <th>Note</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.versions.map((v) => (
                  <tr key={v.version} className={v.is_active ? "selected" : ""}>
                    <td>
                      {v.version} {v.is_active && <span className="crawler-badge crawler-badge--ok">active</span>}
                    </td>
                    <td className="mono">{v.model}</td>
                    <td>
                      {when(v.created_at)}
                      <div className="muted">{v.created_by}</div>
                    </td>
                    <td>{v.note || <span className="muted">—</span>}</td>
                    <td>
                      <button className="crawler-button" onClick={() => void edit(v.version, `version ${v.version}`)}>
                        Edit
                      </button>{" "}
                      {!v.is_active && (
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
          <h2>Examples</h2>
          <p className="muted">Loading one puts it in the editor; nothing changes until you save and activate.</p>
          {shipped.map((example) => (
            <div className="crawler-preset" key={String(example.model)}>
              <button
                className="crawler-button"
                onClick={() => {
                  setText(JSON.stringify(example, null, 2));
                  setEditing(`the example for ${String(example.model)}`);
                  setCheck(null);
                  setSaved(null);
                  setProblem(null);
                }}
              >
                Start from the example for {String(example.model)}
              </button>
            </div>
          ))}
          <button
            className="crawler-button"
            onClick={() => {
              setText(JSON.stringify({ ...EMPTY, model: options.models[0] ?? "" }, null, 2));
              setEditing("a new mapping");
              setCheck(null);
              setSaved(null);
              setProblem(null);
            }}
          >
            Start a new mapping
          </button>
        </details>

        <section className="crawler-editor">
          <div className="crawler-editor-header">
            <h2>
              Edit <span className="muted">(loaded: {editing})</span>
            </h2>
            <div className="crawler-mode" role="radiogroup" aria-label="How to edit">
              <button type="button" role="radio" aria-checked={formMode} className={formMode ? "active" : ""} onClick={() => setFormMode(true)}>
                Form
              </button>
              <button type="button" role="radio" aria-checked={!formMode} className={!formMode ? "active" : ""} onClick={() => setFormMode(false)}>
                JSON
              </button>
            </div>
          </div>
          {formMode && draft === null && (
            <div className="crawler-settings-empty" role="alert">
              The JSON cannot be read, so the form cannot show it. Switch to JSON and fix it, or load a version.
            </div>
          )}
          {formMode && draft !== null && (
            <div className="crawler-form-layout">
              <MappingForm
                mapping={draft}
                model={model}
                models={options.models}
                classes={options.classes}
                onChange={(next) => setText(JSON.stringify(next, null, 2))}
                onProbe={(target, title) => void probe(target, title)}
              />
              <ProbePanel {...probing} />
            </div>
          )}
          {!formMode && (
            <textarea className="crawler-json" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} aria-label="Mapping JSON" />
          )}
          <div className="crawler-editor-actions">
            <button className="crawler-button" disabled={busy} onClick={() => void validate()}>
              Check
            </button>
            <input className="crawler-note" placeholder="Note: what changed and why" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="crawler-button crawler-button--primary" disabled={busy} onClick={() => void save()}>
              Check and save
            </button>
          </div>
          {problem && (
            <pre className="crawler-error" role="alert">
              {problem}
            </pre>
          )}
          {check && (
            <div className={check.valid ? "crawler-success" : "crawler-error"} role="status">
              {check.valid ? "No problems found." : `${check.problems.length} ${check.problems.length === 1 ? "problem" : "problems"}:`}
              {check.problems.length > 0 && (
                <ul>
                  {check.problems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
              {check.not_checked.map((item) => (
                <div key={item} className="muted">
                  Not checked: {item}
                </div>
              ))}
            </div>
          )}
          {saved && (
            <div className="crawler-success" role="status">
              {saved.created ? `Saved as version ${saved.version}.` : `Identical to version ${saved.version}; nothing new saved.`}
              {saved.not_checked.map((item) => (
                <div key={item} className="muted">
                  Not checked: {item}
                </div>
              ))}
              {!saved.is_active && (
                <button className="crawler-button" disabled={busy} onClick={() => void activate(saved.version)}>
                  Activate version {saved.version}
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
