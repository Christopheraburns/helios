import { Navigate, useParams } from "react-router-dom";

import { docsForProject, firstDocSlug } from "../../docs/catalog";
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

export default function DocsProjectPage() {
  const { project: projectParam } = useParams();
  if (!isDocProject(projectParam)) {
    return <Navigate to="/docs" replace />;
  }

  const slug = firstDocSlug(projectParam);
  if (slug) {
    return <Navigate to={`/docs/${projectParam}/${slug}`} replace />;
  }

  const meta = projectMeta(projectParam);
  const count = docsForProject(projectParam).length;

  return (
    <div className="docs-page">
      <h1>{meta.label}</h1>
      <p>{meta.description}</p>
      <p className="docs-muted">
        {count === 0
          ? "No documentation pages are published for this project yet."
          : `${count} pages available.`}
      </p>
    </div>
  );
}
