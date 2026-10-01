import { useEffect, useState } from "react";
import type { HeliosApi, OntologyCheckResult, OntologySchema } from "../../api/client";

interface PublishPanelProps {
  api: () => HeliosApi;
  organizationId: string;
  onPublished: (version: string) => void;
  onClose: () => void;
}

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/**
 * Publish an ontology version (O-6): choose the root schema and a version number,
 * check what would be published (counts, broken mappings, changes from the active
 * version), then publish and optionally activate.
 */
export default function PublishPanel({ api, organizationId, onPublished, onClose }: PublishPanelProps) {
  const [schemas, setSchemas] = useState<OntologySchema[] | null>(null);
  const [schemaPath, setSchemaPath] = useState("");
  const [version, setVersion] = useState("");
  const [check, setCheck] = useState<OntologyCheckResult | null>(null);
  const [published, setPublished] = useState<string | null>(null);
  const [activated, setActivated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api()
      .ontologySchemas!()
      .then((list) => {
        setSchemas(list);
        if (list.length > 0) {
          setSchemaPath(list[0].schema_path);
          setVersion(list[0].version);
        }
      })
      .catch((err) => setError(message(err, "Failed to load schemas")));
  }, [api]);

  const reset = () => {
    setCheck(null);
    setPublished(null);
    setActivated(false);
    setError(null);
  };

  const run = async (step: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await step();
    } catch (err) {
      setError(message(err, "The request failed"));
    } finally {
      setBusy(false);
    }
  };

  if (!organizationId) {
    return (
      <div className="publish-panel">
        <p>Select an organization first: publishing needs the ontology.edit permission in it.</p>
        <button className="link-button" onClick={onClose}>Close</button>
      </div>
    );
  }

  return (
    <div className="publish-panel" role="dialog" aria-label="Publish an ontology version">
      <div className="publish-panel__header">
        <h2>Publish an ontology version</h2>
        <button className="detail-close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <p className="muted">
        Publishing compiles the LinkML files and mappings in the repository into an immutable,
        versioned snapshot recorded in the lakehouse. Activating a version makes it the one the
        crawler and the viewer use.
      </p>

      <div className="publish-panel__form">
        <label>
          Root schema
          <select
            value={schemaPath}
            onChange={(e) => {
              setSchemaPath(e.target.value);
              const chosen = schemas?.find((s) => s.schema_path === e.target.value);
              if (chosen) setVersion(chosen.version);
              reset();
            }}
          >
            {(schemas ?? []).map((s) => (
              <option key={s.schema_path} value={s.schema_path}>
                {s.title ?? s.name} ({s.layer}) — {s.schema_path}
              </option>
            ))}
          </select>
        </label>
        <label>
          Version
          <input
            value={version}
            onChange={(e) => {
              setVersion(e.target.value);
              reset();
            }}
            placeholder="e.g. 0.2.0"
          />
        </label>
        <button
          className="publish-button"
          disabled={busy || !schemaPath || !version.trim()}
          onClick={() =>
            void run(async () => {
              setPublished(null);
              setCheck(await api().checkOntology!(version.trim(), schemaPath, organizationId));
            })
          }
        >
          Check
        </button>
      </div>

      {error && <pre className="publish-error">{error}</pre>}

      {check && (
        <div className="publish-check">
          <div>
            <span className={`badge publish-status publish-status--${check.status}`}>
              {check.status === "new"
                ? "new version"
                : check.status === "identical"
                  ? "already published with this content"
                  : "version number taken by different content"}
            </span>{" "}
            {check.class_count} classes · {check.node_count} nodes · {check.edge_count} edges
          </div>
          {check.status === "conflict" && (
            <p className="publish-warning">
              Version {check.version} is already published with different content, so publishing is
              refused. Choose a new version number.
            </p>
          )}
          {check.broken_mappings.length > 0 ? (
            <div className="publish-warning">
              {check.broken_mappings.length} broken mapping(s):
              <ul>
                {check.broken_mappings.map((b, i) => (
                  <li key={i}>
                    {b.class || "(mapping)"} → {b.mapped_to}: {b.status}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="muted">No broken mappings.</div>
          )}
          {check.changes ? (
            <div className="publish-changes">
              Compared with the active version {check.changes.compared_with}:
              <ul>
                <li>Classes added: {check.changes.classes_added.join(", ") || "none"}</li>
                <li>Classes removed: {check.changes.classes_removed.join(", ") || "none"}</li>
                <li>Mappings added: {check.changes.mappings_added.join(", ") || "none"}</li>
                <li>Mappings removed: {check.changes.mappings_removed.join(", ") || "none"}</li>
                <li>
                  Attributes: +{check.changes.attributes_added} / −{check.changes.attributes_removed}
                </li>
              </ul>
            </div>
          ) : (
            <div className="muted">No active version to compare with.</div>
          )}
          {!check.lakehouse && (
            <p className="publish-warning">
              helios_index is not configured: the version would only go to the local cache.
            </p>
          )}

          {!published && check.status !== "conflict" && (
            <button
              className="publish-button publish-button--primary"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await api().publishOntology!(version.trim(), schemaPath, organizationId);
                  setPublished(result.version);
                  onPublished(result.version);
                })
              }
            >
              {check.status === "identical" ? "Publish (no change)" : `Publish ${version.trim()}`}
            </button>
          )}
          {published && (
            <div className="publish-success">
              Published {published}.{" "}
              {activated ? (
                <strong>Active.</strong>
              ) : (
                <button
                  className="publish-button publish-button--primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api().activateOntology!(published, organizationId);
                      setActivated(true);
                      onPublished(published);
                    })
                  }
                >
                  Activate {published}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
