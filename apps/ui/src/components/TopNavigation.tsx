import { Link } from "react-router-dom";

import { ApplicationContextState } from "../hooks/useApplicationContext";

interface TopNavigationProps {
  context: ApplicationContextState;
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export default function TopNavigation({ context }: TopNavigationProps) {
  const contextParams = new URLSearchParams();
  if (context.selectedOrganizationId) {
    contextParams.set("organization", context.selectedOrganizationId);
  }
  if (context.selectedOrganizationId && context.selectedModelId) {
    contextParams.set("model", context.selectedModelId);
  }
  const contextSearch = contextParams.toString();
  const principalName =
    context.diagnostics?.principal.display_name ||
    context.diagnostics?.principal.subject ||
    "Account";
  const organizationsLoading = context.status === "loading";
  const modelsLoading = context.modelStatus === "loading";

  return (
    <header className="topbar">
      <Link
        className="brand"
        to={{
          pathname: "/",
          search: contextSearch ? `?${contextSearch}` : "",
        }}
        aria-label="Helios overview"
      >
        <span className="brand__mark" aria-hidden="true">
          H
        </span>
        <span className="brand__wordmark">Helios</span>
      </Link>

      <div className="resource-selectors" aria-label="Current workspace">
        <label className="resource-selector">
          <span>Organization</span>
          <select
            aria-label="Organization"
            value={context.selectedOrganizationId}
            disabled={organizationsLoading || context.organizations.length === 0}
            onChange={(event) =>
              context.selectOrganization(event.currentTarget.value)
            }
          >
            {organizationsLoading ? (
              <option value="">Loading organizations…</option>
            ) : context.organizations.length === 0 ? (
              <option value="">No organizations available</option>
            ) : (
              context.organizations.map((organization) => (
                <option key={organization.id} value={organization.id}>
                  {organization.name}
                </option>
              ))
            )}
          </select>
        </label>

        <span className="selector-divider" aria-hidden="true" />

        <label className="resource-selector">
          <span>Model</span>
          <select
            aria-label="Model"
            value={context.selectedModelId}
            disabled={
              !context.selectedOrganizationId ||
              modelsLoading ||
              context.models.length === 0
            }
            onChange={(event) => context.selectModel(event.currentTarget.value)}
          >
            {!context.selectedOrganizationId ? (
              <option value="">Select an organization</option>
            ) : modelsLoading ? (
              <option value="">Loading models…</option>
            ) : context.models.length === 0 ? (
              <option value="">No models available</option>
            ) : (
              context.models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))
            )}
          </select>
        </label>
      </div>

      <div className="account-indicator" title={principalName}>
        <span className="account-indicator__avatar" aria-hidden="true">
          {initials(principalName) || "—"}
        </span>
        <span className="account-indicator__details">
          <span>Signed in</span>
          <strong>{principalName}</strong>
        </span>
      </div>
    </header>
  );
}
