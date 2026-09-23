import { NavLink } from "react-router-dom";

import { ApplicationContextState } from "../hooks/useApplicationContext";

const navigation = [
  { label: "Overview", to: "/", icon: "overview" },
  { label: "Canvas", to: "/canvas", icon: "canvas" },
  { label: "Models", to: "/models", icon: "models" },
  { label: "Data Sources", to: "/data-sources", icon: "data" },
  { label: "Governance", to: "/governance", icon: "governance" },
] as const;

function NavigationIcon({ name }: { name: (typeof navigation)[number]["icon"] }) {
  const paths = {
    overview: <path d="M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm10 0h6v6h-6z" />,
    canvas: <path d="M5 6h5v5H5zm9 7h5v5h-5zM9 9l6 6m-1-9h5m0 0v5" />,
    models: <path d="m12 3 8 4-8 4-8-4zm-8 9 8 4 8-4M4 17l8 4 8-4" />,
    data: <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />,
    governance: <path d="M12 3 5 6v5c0 4.6 2.8 8.4 7 10 4.2-1.6 7-5.4 7-10V6zm-3 9 2 2 4-5" />,
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

interface PrimaryNavigationProps {
  context: ApplicationContextState;
}

export default function PrimaryNavigation({
  context,
}: PrimaryNavigationProps) {
  const contextParams = new URLSearchParams();
  if (context.selectedOrganizationId) {
    contextParams.set("organization", context.selectedOrganizationId);
  }
  if (context.selectedOrganizationId && context.selectedModelId) {
    contextParams.set("model", context.selectedModelId);
  }
  const search = contextParams.toString();

  return (
    <nav className="primary-nav" aria-label="Primary navigation">
      <p className="primary-nav__label">Workspace</p>
      <ul>
        {navigation.map((item) => (
          <li key={item.to}>
            <NavLink
              to={{ pathname: item.to, search: search ? `?${search}` : "" }}
              end={item.to === "/"}
              className={({ isActive }) =>
                `primary-nav__link${isActive ? " primary-nav__link--active" : ""}`
              }
            >
              <NavigationIcon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
      <div className="primary-nav__footer">
        <span className="status-dot" aria-hidden="true" />
        API connected
      </div>
    </nav>
  );
}
