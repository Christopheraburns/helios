import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { ApplicationContextState } from "../hooks/useApplicationContext";

const iconPaths = {
  overview: <path d="M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm10 0h6v6h-6z" />,
  talk: <path d="M4 5h16v11H9l-5 4zm4 4h8M8 12h5" />,
  canvas: <path d="M5 6h5v5H5zm9 7h5v5h-5zM9 9l6 6m-1-9h5m0 0v5" />,
  models: <path d="m12 3 8 4-8 4-8-4zm-8 9 8 4 8-4M4 17l8 4 8-4" />,
  data: <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />,
  governance: <path d="M12 3 5 6v5c0 4.6 2.8 8.4 7 10 4.2-1.6 7-5.4 7-10V6zm-3 9 2 2 4-5" />,
  mcp: <path d="M8 4v4m8-4v4M6 8h12v5a6 6 0 0 1-12 0zm6 11v3m-4 0h8" />,
  activity: <path d="M4 4h16v16H4zM8 9h8M8 13h8M8 17h5" />,
  docs: <path d="M6 4h12v16H6zm2 2v4h8V6zm0 6v6h8v-6z" />,
} as const;

type NavigationIconName = keyof typeof iconPaths;

interface NavigationItem {
  label: string;
  to: string;
  icon: NavigationIconName;
  requiredAction?: string;
  requiresModel?: boolean;
}

interface NavigationGroup {
  label: string;
  items: NavigationItem[];
}

function NavigationIcon({ name }: { name: NavigationIconName }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {iconPaths[name]}
    </svg>
  );
}

interface PrimaryNavigationProps {
  context: ApplicationContextState;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export default function PrimaryNavigation({
  context,
  collapsed,
  onToggleCollapsed,
}: PrimaryNavigationProps) {
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const mobileCloseRef = useRef<HTMLButtonElement>(null);
  const contextParams = new URLSearchParams();
  if (context.selectedOrganizationId) {
    contextParams.set("organization", context.selectedOrganizationId);
  }
  if (context.selectedOrganizationId && context.selectedModelId) {
    contextParams.set("model", context.selectedModelId);
  }
  const search = contextParams.toString();
  const selectedModel = context.models.find(
    (model) => model.id === context.selectedModelId,
  );
  const availableActions = new Set([
    ...(selectedModel?.available_actions ?? []),
    ...(context.modelOverview?.id === context.selectedModelId
      ? context.modelOverview.available_actions
      : []),
  ]);
  const navigation: NavigationGroup[] = [
    {
      label: "Ask",
      items: [
        { label: "Talk to Your Data", to: "/talk", icon: "talk" },
      ],
    },
    {
      label: "Build",
      items: [
        {
          label: "Model Overview",
          to: "/model-overview",
          icon: "overview",
          requiresModel: true,
          requiredAction: "model.read",
        },
        {
          label: "Semantic Model",
          to: "/canvas",
          icon: "canvas",
          requiresModel: true,
          requiredAction: "model.read",
        },
        {
          label: "Models & Discovery",
          to: "/models",
          icon: "models",
          requiresModel: true,
          requiredAction: "model.read",
        },
        {
          label: "Data Sources",
          to: "/data-sources",
          icon: "data",
          requiresModel: true,
          requiredAction: "model.read",
        },
      ],
    },
    {
      label: "Govern",
      items: [
        {
          label: "AI Model Provider",
          to: "/governance/model-provider",
          icon: "models",
        },
        {
          label: "MCP Management",
          to: "/governance/mcp",
          icon: "mcp",
          requiresModel: true,
          requiredAction: "model.read",
        },
        {
          label: "Glossary",
          to: "/governance",
          icon: "governance",
          requiresModel: true,
          requiredAction: "model.read",
        },
        ...(availableActions.has("model.edit")
          && context.modelOverview?.lifecycle.latest_run_id
          ? [{
              label: "Proposals",
              to: "/governance/proposals",
              icon: "governance" as const,
              requiresModel: true,
              requiredAction: "model.edit",
            }]
          : []),
        { label: "Activity Logs", to: "/activity", icon: "activity" },
      ],
    },
    {
      label: "Help",
      items: [{ label: "Documentation", to: "/docs", icon: "docs" }],
    },
  ];
  const visibleNavigation = navigation
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          (!item.requiresModel || Boolean(selectedModel))
          && (!item.requiredAction
            || availableActions.has(item.requiredAction)),
      ),
    }))
    .filter((group) => group.items.length > 0);

  const closeMobileNavigation = useCallback(() => {
    setMobileOpen(false);
    mobileToggleRef.current?.focus();
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname, location.search]);

  useEffect(() => {
    if (!mobileOpen) return;
    mobileCloseRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeMobileNavigation();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [closeMobileNavigation, mobileOpen]);

  return (
    <nav
      className={`primary-nav${collapsed ? " primary-nav--collapsed" : ""}${
        mobileOpen ? " primary-nav--mobile-open" : ""
      }`}
      aria-label="Primary navigation"
      id="workspace-navigation"
    >
      <button
        ref={mobileToggleRef}
        className="primary-nav__mobile-toggle"
        type="button"
        aria-controls="workspace-navigation-drawer"
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen(true)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
        Menu
      </button>
      <button
        className="primary-nav__backdrop"
        type="button"
        aria-label="Close navigation menu"
        onClick={closeMobileNavigation}
      />
      <div className="primary-nav__drawer" id="workspace-navigation-drawer">
        <div className="primary-nav__header">
          <p className="primary-nav__label">Helios</p>
          <button
            ref={mobileCloseRef}
            className="primary-nav__mobile-close"
            type="button"
            aria-label="Close navigation menu"
            onClick={closeMobileNavigation}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
          <button
            className="primary-nav__toggle"
            type="button"
            aria-controls="workspace-navigation"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand workspace" : "Collapse workspace"}
            title={collapsed ? "Expand workspace" : "Collapse workspace"}
            onClick={onToggleCollapsed}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d={collapsed ? "m9 5 7 7-7 7" : "m15 5-7 7 7 7"} />
            </svg>
          </button>
        </div>
        <div className="primary-nav__groups">
          {visibleNavigation.map((group) => (
            <section
              className="primary-nav__group"
              aria-labelledby={`navigation-${group.label.toLowerCase()}`}
              key={group.label}
            >
              <h2 id={`navigation-${group.label.toLowerCase()}`}>
                {group.label}
              </h2>
              <ul>
                {group.items.map((item) => (
                  <li key={item.to}>
                    <NavLink
                      to={{
                        pathname: item.to,
                        search: search ? `?${search}` : "",
                      }}
                      aria-label={collapsed ? item.label : undefined}
                      title={collapsed ? item.label : undefined}
                      onClick={() => {
                        if (!mobileOpen) return;
                        setMobileOpen(false);
                        window.requestAnimationFrame(() => {
                          document.getElementById("main-content")?.focus();
                        });
                      }}
                      className={({ isActive }) =>
                        `primary-nav__link${
                          isActive ? " primary-nav__link--active" : ""
                        }`
                      }
                    >
                      <NavigationIcon name={item.icon} />
                      <span className="primary-nav__link-label">
                        {item.label}
                      </span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <div className="primary-nav__footer">
          <span className="status-dot" aria-hidden="true" />
          API connected
        </div>
      </div>
    </nav>
  );
}
