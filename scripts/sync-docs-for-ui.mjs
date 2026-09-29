#!/usr/bin/env node
/**
 * Copy allowlisted repo markdown into the Helios UI bundle and emit a manifest.
 * Run from apps/helios/ui via predev/prebuild.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const OUT_DIR = path.join(REPO_ROOT, "apps", "helios", "ui", "src", "content", "docs");
const MANIFEST_PATH = path.join(
  REPO_ROOT,
  "apps",
  "helios",
  "ui",
  "src",
  "content",
  "docs-manifest.json",
);

/** @type {Array<{ source: string, slug: string, title: string, project: string, navGroup: string, audience: string, order: number, preamble?: string }>} */
const ALLOWLIST = [
  {
    source: "README.md",
    slug: "overview",
    title: "Helios Query (overview)",
    project: "helios",
    navGroup: "Overview",
    audience: "user",
    order: 10,
    preamble:
      "> **Repository layout:** This overview predates the monorepo layout in some sections. For current paths and the three Cloudera AI projects, see **Platform → Monorepo structure**.\n\n",
  },
  {
    source: "docs/helios-walkthrough.md",
    slug: "walkthrough",
    title: "End-to-end walkthrough",
    project: "helios",
    navGroup: "Getting started",
    audience: "user",
    order: 20,
  },
  {
    source: "docs/console-propose.md",
    slug: "console-propose",
    title: "Reading a proposal",
    project: "helios",
    navGroup: "Console guides",
    audience: "user",
    order: 30,
  },
  {
    source: "docs/console-review.md",
    slug: "console-review",
    title: "Reviewing and publishing",
    project: "helios",
    navGroup: "Console guides",
    audience: "user",
    order: 40,
  },
  {
    source: "docs/console-runs.md",
    slug: "console-runs",
    title: "Reading a discovery run",
    project: "helios",
    navGroup: "Console guides",
    audience: "user",
    order: 50,
  },
  {
    source: "docs/talk-to-your-data-architecture.md",
    slug: "talk-to-your-data",
    title: "Talk to Your Data architecture",
    project: "helios",
    navGroup: "Architecture",
    audience: "developer",
    order: 60,
  },
  {
    source: "docs/mcp-server.md",
    slug: "mcp-server",
    title: "Helios MCP server",
    project: "helios",
    navGroup: "MCP and agents",
    audience: "operator",
    order: 70,
  },
  {
    source: "docs/SECURITY_ARCHITECTURE.md",
    slug: "security",
    title: "Identity and authorization",
    project: "helios",
    navGroup: "Security",
    audience: "operator",
    order: 80,
  },
  {
    source: "docs/ui-deployment.md",
    slug: "ui-deployment",
    title: "API and UI deployment",
    project: "helios",
    navGroup: "Deploy and run",
    audience: "operator",
    order: 90,
  },
  {
    source: "docs/ui-api-networking.md",
    slug: "ui-api-networking",
    title: "UI-to-API networking",
    project: "helios",
    navGroup: "Deploy and run",
    audience: "operator",
    order: 100,
  },
  {
    source: "docs/operational-metadata.md",
    slug: "operational-metadata",
    title: "Operational metadata",
    project: "helios",
    navGroup: "Operations",
    audience: "operator",
    order: 110,
  },
  {
    source: "docs/helios-metadata-database.md",
    slug: "metadata-database",
    title: "Operational metadata database",
    project: "helios",
    navGroup: "Operations",
    audience: "operator",
    order: 120,
  },
  {
    source: "docs/ui-backend-contract.md",
    slug: "ui-backend-contract",
    title: "UI backend contract",
    project: "helios",
    navGroup: "API reference",
    audience: "developer",
    order: 130,
  },
  {
    source: "docs/model-graph-api.md",
    slug: "model-graph-api",
    title: "Model graph API",
    project: "helios",
    navGroup: "API reference",
    audience: "developer",
    order: 140,
  },
  {
    source: "docs/model-artifacts.md",
    slug: "model-artifacts",
    title: "Model-aware artifacts",
    project: "helios",
    navGroup: "API reference",
    audience: "developer",
    order: 150,
  },
  {
    source: "docs/legacy-ui-migration.md",
    slug: "legacy-ui-migration",
    title: "Legacy console UI migration",
    project: "helios",
    navGroup: "Engineering notes",
    audience: "developer",
    order: 160,
  },
  {
    source: "MONOREPO.md",
    slug: "monorepo",
    title: "Monorepo structure",
    project: "platform",
    navGroup: "Monorepo",
    audience: "operator",
    order: 10,
  },
  {
    source: "docs/helios-ds-burndown.md",
    slug: "burndown",
    title: "Generation roadmap and burndown",
    project: "helios-ds-generation",
    navGroup: "Overview and roadmap",
    audience: "developer",
    order: 10,
  },
  {
    source: "docs/helios-ds-evaluation.md",
    slug: "overview",
    title: "Evaluation harness overview",
    project: "helios-ds-evaluation",
    navGroup: "Overview",
    audience: "user",
    order: 10,
  },
];

function stripMarkdownForExcerpt(text, maxLen = 240) {
  const plain = text
    .replace(/^---[\s\S]*?---\n/m, "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]+`/g, " ")
    .replace(/!\[[^\]]*]\([^)]+\)/g, " ")
    .replace(/\[[^\]]+]\([^)]+\)/g, " ")
    .replace(/^#+\s+/gm, "")
    .replace(/[*_~>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (plain.length <= maxLen) return plain;
  return `${plain.slice(0, maxLen - 1).trim()}…`;
}

function buildFrontmatter(entry) {
  const lines = [
    "---",
    `title: ${JSON.stringify(entry.title)}`,
    `project: ${entry.project}`,
    `navGroup: ${JSON.stringify(entry.navGroup)}`,
    `audience: ${entry.audience}`,
    `order: ${entry.order}`,
    `sourcePath: ${JSON.stringify(entry.source)}`,
    "---",
    "",
  ];
  return lines.join("\n");
}

function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  for (const name of fs.readdirSync(OUT_DIR)) {
    if (name.endsWith(".md")) {
      fs.unlinkSync(path.join(OUT_DIR, name));
    }
  }

  /** @type {import('../apps/helios/ui/src/docs/types').DocManifestEntry[]} */
  const manifest = [];

  for (const entry of ALLOWLIST) {
    const sourcePath = path.join(REPO_ROOT, entry.source);
    if (!fs.existsSync(sourcePath)) {
      console.error(`sync-docs-for-ui: missing source ${entry.source}`);
      process.exit(1);
    }
    const body = fs.readFileSync(sourcePath, "utf8");
    const preamble = entry.preamble ?? "";
    const outName = `${entry.project}__${entry.slug}.md`;
    const outPath = path.join(OUT_DIR, outName);
    fs.writeFileSync(outPath, buildFrontmatter(entry) + preamble + body, "utf8");

    manifest.push({
      id: `${entry.project}/${entry.slug}`,
      project: entry.project,
      slug: entry.slug,
      title: entry.title,
      navGroup: entry.navGroup,
      audience: entry.audience,
      order: entry.order,
      sourcePath: entry.source,
      excerpt: stripMarkdownForExcerpt(body),
      bundlePath: `./docs/${outName}`,
    });
  }

  manifest.sort((a, b) => {
    if (a.project !== b.project) return a.project.localeCompare(b.project);
    if (a.order !== b.order) return a.order - b.order;
    return a.title.localeCompare(b.title);
  });

  fs.writeFileSync(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  console.log(`sync-docs-for-ui: wrote ${manifest.length} pages to ${OUT_DIR}`);
}

main();
