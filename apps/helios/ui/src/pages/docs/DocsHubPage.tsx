import { Link } from "react-router-dom";
import { useMemo, useState } from "react";

import { firstDocSlug, searchDocs } from "../../docs/catalog";
import { DOC_PROJECTS } from "../../docs/types";

export default function DocsHubPage() {
  const [query, setQuery] = useState("");
  const results = useMemo(() => searchDocs(query), [query]);

  return (
    <div className="docs-page docs-page--hub">
      <header className="docs-page__header">
        <h1>Documentation</h1>
        <p>
          Guides and reference for Helios Query, Helios-DS-Generation,
          Helios-DS-Evaluation, and the monorepo platform.
        </p>
      </header>

      <label className="docs-search docs-search--wide">
        <span className="docs-search__label">Search all documentation</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search titles and excerpts…"
        />
      </label>

      {query.trim() ? (
        <section className="docs-hub-results" aria-label="Search results">
          {results.length === 0 ? (
            <p className="docs-muted">No pages match your search.</p>
          ) : (
            <ul className="docs-hub-results__list">
              {results.map((entry) => (
                <li key={entry.id}>
                  <Link to={`/docs/${entry.project}/${entry.slug}`}>
                    <strong>{entry.title}</strong>
                  </Link>
                  <span className="docs-hub-results__meta">
                    {DOC_PROJECTS.find((p) => p.id === entry.project)?.label}
                  </span>
                  <p>{entry.excerpt}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <div className="docs-hub-grid">
          {DOC_PROJECTS.map((project) => {
            const firstSlug = firstDocSlug(project.id);
            const target = firstSlug
              ? `/docs/${project.id}/${firstSlug}`
              : `/docs/${project.id}`;
            return (
              <Link
                key={project.id}
                className="docs-hub-card"
                to={target}
              >
                <h2>{project.label}</h2>
                <p>{project.description}</p>
                <span className="docs-hub-card__cta">Browse →</span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
