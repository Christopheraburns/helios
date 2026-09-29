import { Link, Navigate, useParams } from "react-router-dom";
import { useState } from "react";

import DocsSidebar from "../../components/docs/DocsSidebar";
import MarkdownView from "../../components/docs/MarkdownView";
import { docEntry, docMarkdown } from "../../docs/catalog";
import { projectMeta, type DocProject } from "../../docs/types";

const PROJECT_IDS = new Set([
  "helios",
  "helios-ds-generation",
  "helios-ds-evaluation",
  "platform",
]);

function isDocProject(value: string | undefined): value is DocProject {
  return value !== undefined && PROJECT_IDS.has(value);
}

export default function DocsArticlePage() {
  const { project: projectParam, slug } = useParams();
  const [sidebarSearch, setSidebarSearch] = useState("");

  if (!isDocProject(projectParam) || !slug) {
    return <Navigate to="/docs" replace />;
  }

  const entry = docEntry(projectParam, slug);
  if (!entry) {
    return <Navigate to="/docs" replace />;
  }

  const meta = projectMeta(projectParam);
  let markdown: string;
  try {
    markdown = docMarkdown(entry);
  } catch {
    return (
      <div className="docs-page">
        <p className="docs-error" role="alert">
          This page is missing from the UI build. Run{" "}
          <code>npm run build</code> or <code>npm run dev</code> in{" "}
          <code>apps/helios/ui</code> to sync documentation from the repo.
        </p>
      </div>
    );
  }

  return (
    <div className="docs-layout">
      <DocsSidebar
        project={projectParam}
        searchQuery={sidebarSearch}
        onSearchQueryChange={setSidebarSearch}
      />
      <div className="docs-layout__main">
        <nav className="docs-breadcrumbs" aria-label="Breadcrumb">
          <Link to="/docs">Documentation</Link>
          <span aria-hidden="true">/</span>
          <Link to={`/docs/${projectParam}/${slug}`}>{meta.label}</Link>
        </nav>
        <header className="docs-article-header">
          <h1>{entry.title}</h1>
          <p className="docs-article-header__meta">
            <span>{entry.navGroup}</span>
            <span aria-hidden="true">·</span>
            <span className="docs-audience">{entry.audience}</span>
            <span aria-hidden="true">·</span>
            <span className="docs-source" title="Source file in git">
              {entry.sourcePath}
            </span>
          </p>
        </header>
        <MarkdownView markdown={markdown} />
      </div>
    </div>
  );
}
