import { useEffect, useMemo, useState } from "react";
import type {
  OntologyClassDetail,
  OntologyGraphPayload,
  OntologyVersionSummary,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import OntologyGraphView, { LAYER_COLOURS } from "../features/ontology/OntologyGraphView";
import {
  brokenClasses,
  buildOntologyGraphModel,
  LAYERS,
  mappingsFor,
  type Layer,
} from "../features/ontology/ontologyGraphModel";
import type { ApplicationContextState } from "../hooks/useApplicationContext";
import "./OntologyPage.css";

interface OntologyPageProps {
  context: ApplicationContextState;
}

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export default function OntologyPage({ context }: OntologyPageProps) {
  const { loadOntologyVersions, loadOntologyGraph, loadOntologyClass } = context;
  const [versions, setVersions] = useState<OntologyVersionSummary[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [graph, setGraph] = useState<OntologyGraphPayload | null>(null);
  const [classFilter, setClassFilter] = useState("");
  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [classDetail, setClassDetail] = useState<OntologyClassDetail | null>(null);
  const [layers, setLayers] = useState<Set<Layer>>(new Set(LAYERS));
  const [showRanges, setShowRanges] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await loadOntologyVersions();
        if (cancelled) return;
        setVersions(data);
        const active = data.find((v) => v.is_active) ?? data[0];
        if (active) setSelectedVersion(active.version);
      } catch (err) {
        if (!cancelled) setError(message(err, "Failed to load versions"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadOntologyVersions]);

  useEffect(() => {
    if (!selectedVersion) return;
    let cancelled = false;
    setGraph(null);
    setSelectedClass(null);
    setClassDetail(null);
    (async () => {
      try {
        const data = await loadOntologyGraph(selectedVersion);
        if (!cancelled) setGraph(data);
      } catch (err) {
        if (!cancelled) setError(message(err, "Failed to load graph"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedVersion, loadOntologyGraph]);

  useEffect(() => {
    if (!selectedVersion || !selectedClass) {
      setClassDetail(null);
      return;
    }
    let cancelled = false;
    setClassDetail(null);
    (async () => {
      try {
        const detail = await loadOntologyClass(selectedVersion, selectedClass);
        if (!cancelled) setClassDetail(detail);
      } catch (err) {
        if (!cancelled) setError(message(err, "Failed to load class detail"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedVersion, selectedClass, loadOntologyClass]);

  const model = useMemo(() => (graph ? buildOntologyGraphModel(graph) : null), [graph]);
  const classes = useMemo(
    () => [...(model?.classes ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [model],
  );
  const filteredClasses = classes.filter(
    (c) => layers.has(c.layer) && c.name.toLowerCase().includes(classFilter.toLowerCase()),
  );
  const selectedNode = model?.classes.find((c) => c.name === selectedClass) ?? null;
  const broken = useMemo(() => (graph ? brokenClasses(graph) : new Set<string>()), [graph]);
  const mappings = useMemo(
    () => (graph && selectedClass ? mappingsFor(graph, selectedClass) : []),
    [graph, selectedClass],
  );
  const related = useMemo(() => {
    if (!model || !selectedClass) return { outgoing: [], incoming: [], children: [] };
    return {
      outgoing: model.links.filter((l) => l.kind === "range" && l.source === selectedClass),
      incoming: model.links.filter((l) => l.kind === "range" && l.target === selectedClass),
      children: model.links
        .filter((l) => l.kind === "is_a" && l.target === selectedClass)
        .map((l) => l.source)
        .sort(),
    };
  }, [model, selectedClass]);

  const toggleLayer = (layer: Layer) =>
    setLayers((current) => {
      const next = new Set(current);
      if (next.has(layer)) next.delete(layer);
      else next.add(layer);
      return next;
    });

  if (loading) {
    return (
      <div className="ontology-page">
        <LoadingState />
      </div>
    );
  }

  if (error) {
    return (
      <div className="ontology-page">
        <ErrorState title="Failed to load ontology" message={error} />
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="ontology-page">
        <div className="empty-state">
          <h2>No ontology versions published</h2>
          <p>Publish a LinkML schema to get started.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="ontology-page">
      <div className="ontology-header">
        <h1>Ontology Browser</h1>

        <div className="ontology-toolbar">
          <div className="version-selector">
            <label htmlFor="version-select">Version:</label>
            <select
              id="version-select"
              value={selectedVersion || ""}
              onChange={(e) => setSelectedVersion(e.target.value)}
            >
              {versions.map((v) => (
                <option key={v.version} value={v.version}>
                  {v.version}
                  {v.is_active ? " (active)" : ""}
                  {` • ${v.node_count} nodes, ${v.edge_count} edges`}
                </option>
              ))}
            </select>
          </div>

          <fieldset className="layer-filter">
            <legend>Layers</legend>
            {LAYERS.map((layer) => (
              <label key={layer} className="layer-toggle">
                <input
                  type="checkbox"
                  checked={layers.has(layer)}
                  onChange={() => toggleLayer(layer)}
                />
                <span
                  className="layer-swatch"
                  style={{
                    background: LAYER_COLOURS[layer].fill,
                    borderColor: LAYER_COLOURS[layer].border,
                  }}
                />
                {LAYER_COLOURS[layer].label}
              </label>
            ))}
          </fieldset>

          <label className="range-toggle">
            <input
              type="checkbox"
              checked={showRanges}
              onChange={(e) => setShowRanges(e.target.checked)}
            />
            Show relationships (attribute ranges)
          </label>
        </div>
        <p className="ontology-legend">
          Grey arrows: <strong>is_a</strong> (arrow at the parent). Orange dashed: an attribute
          whose values are another class (<code>*</code> = many). Click a class to focus on its
          connections; click the background to clear.
        </p>
      </div>

      <div className={`ontology-content ${selectedClass ? "with-inspector" : ""}`}>
        <div className="class-browser">
          <div className="class-search">
            <input
              type="text"
              placeholder="Search classes..."
              value={classFilter}
              onChange={(e) => setClassFilter(e.target.value)}
            />
            <div className="class-count">
              {filteredClasses.length} of {classes.length}
            </div>
          </div>

          <div className="class-list">
            {filteredClasses.map((c) => (
              <div
                key={c.name}
                className={`class-item ${selectedClass === c.name ? "selected" : ""}`}
                onClick={() => setSelectedClass(c.name)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") setSelectedClass(c.name);
                }}
              >
                <span
                  className="layer-swatch"
                  style={{
                    background: LAYER_COLOURS[c.layer].fill,
                    borderColor: LAYER_COLOURS[c.layer].border,
                  }}
                />
                {c.name}
              </div>
            ))}
          </div>
        </div>

        {model ? (
          <OntologyGraphView
            model={model}
            broken={broken}
            layers={layers}
            showRanges={showRanges}
            selected={selectedClass}
            search={classFilter}
            onSelect={setSelectedClass}
          />
        ) : (
          <div className="ontology-graph">
            <LoadingState />
          </div>
        )}

        {selectedClass && (
          <div className="class-detail">
            <div className="detail-header">
              <h2>{selectedClass}</h2>
              {selectedNode && (
                <div className="detail-meta">
                  <span className="badge">{LAYER_COLOURS[selectedNode.layer].label}</span>
                  <span className="badge">{selectedNode.kind}</span>
                  {selectedNode.abstract && <span className="badge">abstract</span>}
                </div>
              )}
              <button className="detail-close" onClick={() => setSelectedClass(null)} aria-label="Close">
                ×
              </button>
            </div>

            {selectedNode?.description && (
              <div className="detail-section">
                <p className="detail-description">{selectedNode.description}</p>
              </div>
            )}

            {!classDetail && <LoadingState />}

            {classDetail && classDetail.parents.length > 0 && (
              <div className="detail-section">
                <h3>Parents (is_a)</h3>
                <ul>
                  {classDetail.parents.map((parent) => (
                    <li key={parent}>
                      <button className="link-button" onClick={() => setSelectedClass(parent)}>
                        {parent}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {related.children.length > 0 && (
              <div className="detail-section">
                <h3>Subclasses</h3>
                <ul>
                  {related.children.map((child) => (
                    <li key={child}>
                      <button className="link-button" onClick={() => setSelectedClass(child)}>
                        {child}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {mappings.length > 0 && (
              <div className="detail-section">
                <h3>Mapped from the warehouse (Ossie)</h3>
                <ul className="mapping-list">
                  {mappings.map((m) => (
                    <li key={`${m.kind}:${m.element}`} className={m.status !== "ok" ? "broken" : ""}>
                      <code>{m.element}</code> <span className="badge">{m.kind}</span>
                      {m.status !== "ok" && <span className="badge broken">missing from model</span>}
                      {m.kind === "dataset" && (
                        <div className="mapping-identifiers">
                          key: {m.primary.join(" + ")}
                          {m.secondary.length > 0 && <> · also: {m.secondary.join(", ")}</>}
                          {m.display.length > 0 && <> · name: {m.display.join(" ")}</>}
                          {m.aliases.length > 0 && <> · aliases: {m.aliases.join(", ")}</>}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(related.outgoing.length > 0 || related.incoming.length > 0) && (
              <div className="detail-section">
                <h3>Relationships</h3>
                <ul>
                  {related.outgoing.map((l) => (
                    <li key={l.id}>
                      {l.label}
                      {l.multivalued ? " (many)" : ""} →{" "}
                      <button className="link-button" onClick={() => setSelectedClass(l.target)}>
                        {l.target}
                      </button>
                    </li>
                  ))}
                  {related.incoming.map((l) => (
                    <li key={l.id}>
                      <button className="link-button" onClick={() => setSelectedClass(l.source)}>
                        {l.source}
                      </button>
                      .{l.label} → this
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {classDetail && classDetail.attributes.length > 0 && (
              <div className="detail-section">
                <h3>Attributes</h3>
                <div className="attributes-list">
                  {classDetail.attributes.map((attr) => {
                    const range = classDetail.ranges.find((r) => r.attribute === attr.name);
                    const props = attr.properties ?? {};
                    return (
                      <div
                        key={attr.name}
                        className={`attribute-item ${props.inherited ? "inherited" : ""}`}
                      >
                        <div className="attribute-name">{attr.name.split("#")[1] || attr.name}</div>
                        {range && <div className="attribute-range">→ {range.range_class}</div>}
                        <div className="attribute-properties">
                          {Boolean(props.multivalued) && <span className="badge">multivalued</span>}
                          {Boolean(props.identifier) && (
                            <span className="badge identifier">identifier</span>
                          )}
                          {Boolean(props.inherited) && typeof props.declared_by === "string" && (
                            <span className="badge">from {props.declared_by}</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
