import { EmptyState, ErrorState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface OverviewPageProps {
  context: ApplicationContextState;
}

export default function OverviewPage({ context }: OverviewPageProps) {
  const organization = context.organizations.find(
    (item) => item.id === context.selectedOrganizationId,
  );
  const model = context.models.find(
    (item) => item.id === context.selectedModelId,
  );

  if (context.organizations.length === 0) {
    return (
      <EmptyState
        title="No organizations available"
        message="Your account does not currently have access to a Helios organization."
        actionLabel="Refresh access"
        onAction={context.retry}
      />
    );
  }

  if (context.modelStatus === "error") {
    return (
      <ErrorState
        title="Models could not be loaded"
        message={context.errorMessage ?? "The API request failed."}
        onRetry={context.retry}
      />
    );
  }

  return (
    <>
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">{organization?.name}</p>
          <h1>Overview</h1>
          <p>
            Review the current semantic workspace and its governed resources.
          </p>
        </div>
        <span className="environment-badge">
          <span aria-hidden="true" />
          Connected
        </span>
      </header>

      {context.modelStatus === "loading" ? (
        <div className="summary-grid" aria-label="Loading model summary">
          {[0, 1, 2].map((item) => (
            <div className="summary-card summary-card--loading" key={item}>
              <span />
              <span />
            </div>
          ))}
        </div>
      ) : !model ? (
        <EmptyState
          title="No models available"
          message="This organization does not contain a model accessible to your account."
          actionLabel="Refresh models"
          onAction={context.retry}
        />
      ) : (
        <>
          <section className="model-hero" aria-labelledby="current-model-title">
            <div>
              <p className="model-hero__label">Current model</p>
              <h2 id="current-model-title">{model.name}</h2>
              <p>
                {model.description ||
                  "No description has been provided for this model."}
              </p>
            </div>
            <span className="model-hero__id">{model.id}</span>
          </section>

          <section className="summary-grid" aria-label="Model summary">
            <article className="summary-card">
              <p>Data sources</p>
              <strong>{model.data_sources.length}</strong>
              <span>Connected to this model</span>
            </article>
            <article className="summary-card">
              <p>Selected assets</p>
              <strong>
                {model.data_sources.reduce(
                  (total, source) => total + source.selected_assets.length,
                  0,
                )}
              </strong>
              <span>Available through the API</span>
            </article>
            <article className="summary-card">
              <p>Permitted actions</p>
              <strong>{model.available_actions.length}</strong>
              <span>Granted for your account</span>
            </article>
          </section>
        </>
      )}
    </>
  );
}
