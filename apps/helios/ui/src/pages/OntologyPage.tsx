import { useEffect, useState } from "react";
import type {
  OntologyClassDetail,
  OntologyVersionSummary,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
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
  const [classFilter, setClassFilter] = useState("");
  const [classNames, setClassNames] = useState<string[]>([]);
  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [classDetail, setClassDetail] = useState<OntologyClassDetail | null>(null);
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
    setClassNames([]);
    setSelectedClass(null);
    setClassDetail(null);
    (async () => {
      try {
        const graph = await loadOntologyGraph(selectedVersion);
        if (cancelled) return;
        setClassNames(
          graph.nodes
            .filter((n) => n.label === "Class")
            .map((n) => n.key)
            .sort(),
        );
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

  const filteredClasses = classNames.filter((name) =>
    name.toLowerCase().includes(classFilter.toLowerCase())
  );

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
      </div>

      <div className="ontology-content">
        {/* Class browser */}
        <div className="class-browser">
          <div className="class-search">
            <input
              type="text"
              placeholder="Search classes..."
              value={classFilter}
              onChange={(e) => setClassFilter(e.target.value)}
            />
            <div className="class-count">
              {filteredClasses.length} of {classNames.length}
            </div>
          </div>

          <div className="class-list">
            {filteredClasses.map((name) => (
              <div
                key={name}
                className={`class-item ${
                  selectedClass === name ? "selected" : ""
                }`}
                onClick={() => setSelectedClass(name)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    setSelectedClass(name);
                  }
                }}
              >
                {name}
              </div>
            ))}
          </div>
        </div>

        {/* Class detail panel */}
        {classDetail && (
          <div className="class-detail">
            <div className="detail-header">
              <h2>{classDetail.class.name}</h2>
            </div>

            {classDetail.parents.length > 0 && (
              <div className="detail-section">
                <h3>Parents (is_a)</h3>
                <ul>
                  {classDetail.parents.map((parent) => (
                    <li key={parent}>{parent}</li>
                  ))}
                </ul>
              </div>
            )}

            {classDetail.attributes.length > 0 && (
              <div className="detail-section">
                <h3>Attributes</h3>
                <div className="attributes-list">
                  {classDetail.attributes.map((attr) => {
                    const range = classDetail.ranges.find(
                      (r) => r.attribute === attr.name
                    );
                    return (
                      <div key={attr.name} className="attribute-item">
                        <div className="attribute-name">
                          {attr.name.split("#")[1] || attr.name}
                        </div>
                        {range && (
                          <div className="attribute-range">
                            → {range.range_class}
                          </div>
                        )}
                        {attr.properties && (
                          <div className="attribute-properties">
                            {(attr.properties.multivalued as boolean) && (
                              <span className="badge">multivalued</span>
                            )}
                            {(attr.properties.identifier as boolean) && (
                              <span className="badge identifier">identifier</span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {classDetail.attributes.length === 0 &&
              classDetail.parents.length === 0 && (
                <div className="empty-detail">
                  <p>No attributes or parents</p>
                </div>
              )}
          </div>
        )}

        {!classDetail && selectedClass && (
          <div className="class-detail">
            <LoadingState />
          </div>
        )}

        {!selectedClass && (
          <div className="class-detail empty">
            <div className="placeholder">Select a class to view details</div>
          </div>
        )}
      </div>
    </div>
  );
}
