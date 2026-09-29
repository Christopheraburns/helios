import { NavLink } from "react-router-dom";

import { docsForProject } from "../../docs/catalog";
import type { DocProject } from "../../docs/types";

interface DocsSidebarProps {
  project: DocProject;
  searchQuery: string;
  onSearchQueryChange: (value: string) => void;
}

export default function DocsSidebar({
  project,
  searchQuery,
  onSearchQueryChange,
}: DocsSidebarProps) {
  const entries = docsForProject(project);
  const groups = new Map<string, typeof entries>();
  for (const entry of entries) {
    const list = groups.get(entry.navGroup) ?? [];
    list.push(entry);
    groups.set(entry.navGroup, list);
  }

  const filteredGroups = [...groups.entries()].map(([label, items]) => {
    const normalized = searchQuery.trim().toLowerCase();
    const visible = normalized
      ? items.filter((item) =>
          `${item.title} ${item.excerpt}`.toLowerCase().includes(normalized),
        )
      : items;
    return [label, visible] as const;
  }).filter(([, items]) => items.length > 0);

  return (
    <aside className="docs-sidebar" aria-label="Documentation navigation">
      <label className="docs-search">
        <span className="docs-search__label">Search this project</span>
        <input
          type="search"
          value={searchQuery}
          onChange={(event) => onSearchQueryChange(event.target.value)}
          placeholder="Filter pages…"
        />
      </label>
      <nav className="docs-sidebar__nav">
        {filteredGroups.map(([groupLabel, items]) => (
          <section className="docs-sidebar__group" key={groupLabel}>
            <h2>{groupLabel}</h2>
            <ul>
              {items.map((item) => (
                <li key={item.id}>
                  <NavLink
                    to={`/docs/${project}/${item.slug}`}
                    className={({ isActive }) =>
                      `docs-sidebar__link${
                        isActive ? " docs-sidebar__link--active" : ""
                      }`
                    }
                  >
                    {item.title}
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>
    </aside>
  );
}
