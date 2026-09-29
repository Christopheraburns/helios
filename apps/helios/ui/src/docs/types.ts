export type DocProject =
  | "helios"
  | "helios-ds-generation"
  | "helios-ds-evaluation"
  | "platform";

export type DocAudience = "user" | "operator" | "developer";

export interface DocManifestEntry {
  id: string;
  project: DocProject;
  slug: string;
  title: string;
  navGroup: string;
  audience: DocAudience;
  order: number;
  sourcePath: string;
  excerpt: string;
  bundlePath: string;
}

export interface DocProjectMeta {
  id: DocProject;
  label: string;
  description: string;
}

export const DOC_PROJECTS: DocProjectMeta[] = [
  {
    id: "helios",
    label: "Helios Query",
    description:
      "Semantic layer, glossary, console, MCP, and talk-to-your-data on Cloudera.",
  },
  {
    id: "helios-ds-generation",
    label: "Helios-DS-Generation",
    description:
      "Deterministic synthetic enterprise data and review workflows.",
  },
  {
    id: "helios-ds-evaluation",
    label: "Helios-DS-Evaluation",
    description:
      "Ground-truth benchmarking in an isolated Workbench project.",
  },
  {
    id: "platform",
    label: "Platform",
    description: "Monorepo layout, shared core, and cross-project deployment.",
  },
];

export function projectMeta(id: DocProject): DocProjectMeta {
  const meta = DOC_PROJECTS.find((item) => item.id === id);
  if (!meta) throw new Error(`Unknown doc project: ${id}`);
  return meta;
}
