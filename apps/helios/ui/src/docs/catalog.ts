import manifest from "../content/docs-manifest.json";
import type { DocManifestEntry, DocProject } from "./types";

const rawModules = import.meta.glob("../content/docs/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

export const docManifest = manifest as DocManifestEntry[];

export function docsForProject(project: DocProject): DocManifestEntry[] {
  return docManifest
    .filter((entry) => entry.project === project)
    .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
}

export function docEntry(
  project: DocProject,
  slug: string,
): DocManifestEntry | undefined {
  return docManifest.find(
    (entry) => entry.project === project && entry.slug === slug,
  );
}

export function docMarkdown(entry: DocManifestEntry): string {
  const key = `../content/docs/${entry.project}__${entry.slug}.md`;
  const content = rawModules[key];
  if (content === undefined) {
    throw new Error(`Missing bundled doc: ${key}`);
  }
  return stripFrontmatter(content);
}

function stripFrontmatter(source: string): string {
  if (!source.startsWith("---\n")) return source;
  const end = source.indexOf("\n---\n", 4);
  if (end === -1) return source;
  return source.slice(end + 5);
}

export function searchDocs(query: string): DocManifestEntry[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return [];
  return docManifest.filter((entry) => {
    const haystack = `${entry.title} ${entry.excerpt} ${entry.navGroup}`.toLowerCase();
    return haystack.includes(normalized);
  });
}

export function firstDocSlug(project: DocProject): string | undefined {
  return docsForProject(project)[0]?.slug;
}
