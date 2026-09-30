import { useEffect, useState } from "react";
import { useApplicationContext } from "../hooks/useApplicationContext";
import { ErrorState, LoadingState } from "../components/AsyncState";
import "./OntologyPage.css";

interface OntologyVersion {
  version: string;
  content_hash: string;
  node_count: number;
  edge_count: number;
  enum_count: number;
  is_active: boolean;
}

interface ClassDetail {
  class: {
    name: string;
    properties: Record<string, unknown>;
  };
  parents: string[];
  attributes: Array<{
    name: string;
    properties: Record<string, unknown>;
  }>;
  ranges: Array<{
    attribute: string;
    range_class: string;
  }>;
}

export default function OntologyPage() {
  const context = useApplicationContext();
  const [versions, setVersions] = useState<OntologyVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [classFilter, setClassFilter] = useState("");
  const [classNames, setClassNames] = useState<string[]>([]);
  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [classDetail, setClassDetail] = useState<ClassDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch versions on mount
  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await fetch("/api/v1/ontology/versions");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data: OntologyVersion[] = await response.json();
        setVersions(data);
        if (data.length > 0) {
          setSelectedVersion(data[0].version);
        }
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load versions"
        );
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Load class list when version changes
  useEffect(() => {
    if (!selectedVersion) return;

    (async () => {
      try {
        setClassNames([]);
        setSelectedClass(null);
        setClassDetail(null);

        const response = await fetch(`/api/v1/ontology/${selectedVersion}/graph`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const graph = await response.json();

        const classes = graph.nodes
          .filter((n: { label: string }) => n.label === "Class")
          .map((n: { key: string }) => n.key)
          .sort();

        setClassNames(classes);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load graph"
        );
      }
    })();
  }, [selectedVersion]);

  // Load class detail when selection changes
  useEffect(() => {
    if (!selectedVersion || !selectedClass) {
      setClassDetail(null);
      return;
    }

    (async () => {
      try {
        const response = await fetch(
          `/api/v1/ontology/${selectedVersion}/classes/${selectedClass}`
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const detail: ClassDetail = await response.json();
        setClassDetail(detail);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load class detail"
        );
      }
    })();
  }, [selectedVersion, selectedClass]);

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
                            {attr.properties.multivalued && (
                              <span className="badge">multivalued</span>
                            )}
                            {attr.properties.identifier && (
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
