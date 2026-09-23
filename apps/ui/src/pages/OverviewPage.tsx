import { Link, useLocation } from "react-router-dom";

import { EmptyState, ErrorState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface OverviewPageProps {
  context: ApplicationContextState;
}

export default function OverviewPage({ context }: OverviewPageProps) {
  const location = useLocation();
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

  const overview = context.modelOverview;
  const actions = new Set(overview?.available_actions ?? []);
  const reviewUrl =
    overview?.lifecycle.latest_run_id && context.applicationUrl
      ? context.applicationUrl(
          `/runs/${encodeURIComponent(overview.lifecycle.latest_run_id)}/review`,
        )
      : undefined;

  return (
    <>
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">{organization?.name}</p>
          <h1>{model?.name ?? "Model overview"}</h1>
          <p>{model?.description || "Review this governed semantic model."}</p>
        </div>
        {overview ? (
          <span className={`lifecycle-badge lifecycle-badge--${overview.lifecycle.publication_state}`}>
            {formatLabel(overview.lifecycle.publication_state)}
          </span>
        ) : null}
      </header>

      {context.modelStatus === "loading" ||
      (model && context.overviewStatus === "loading") ? (
        <div className="summary-grid" aria-label="Loading model overview">
          {[0, 1, 2, 3].map((item) => (
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
      ) : context.overviewStatus === "error" ? (
        <ErrorState
          title="Model overview could not be loaded"
          message={context.errorMessage ?? "The API request failed."}
          onRetry={context.retry}
        />
      ) : overview ? (
        <>
          <section className="overview-actions" aria-label="Model actions">
            <div>
              <p className="section-eyebrow">Model workspace</p>
              <h2>What would you like to do?</h2>
            </div>
            <div className="overview-actions__buttons">
              {actions.has("model.read") ? (
                <Link
                  className="button button--primary"
                  to={{ pathname: "/canvas", search: location.search }}
                >
                  Open Canvas
                </Link>
              ) : null}
              {actions.has("discovery.run") ? (
                <button
                  className="button button--secondary"
                  disabled
                  title="The API does not yet expose a discovery launch operation."
                  type="button"
                >
                  Run Discovery
                </button>
              ) : null}
              {reviewUrl &&
              actions.has("model.edit") &&
              overview.lifecycle.review_status === "pending" ? (
                <a className="button button--secondary" href={reviewUrl}>
                  Review Proposals
                </a>
              ) : null}
              {reviewUrl &&
              actions.has("model.publish") &&
              overview.lifecycle.review_status === "complete" ? (
                <a className="button button--secondary" href={reviewUrl}>
                  Publish
                </a>
              ) : null}
            </div>
          </section>

          <section className="summary-grid summary-grid--four" aria-label="Model summary">
            <SummaryCard label="Datasets" value={overview.summary.dataset_count} />
            <SummaryCard
              label="Relationships"
              value={overview.summary.relationship_count}
            />
            <SummaryCard label="Concepts" value={overview.summary.concept_count} />
            <SummaryCard label="Metrics" value={overview.summary.metric_count} />
          </section>

          <div className="overview-columns">
            <section className="overview-panel" aria-labelledby="model-details-title">
              <div className="overview-panel__header">
                <div>
                  <p className="section-eyebrow">Governance</p>
                  <h2 id="model-details-title">Model details</h2>
                </div>
                <span className="model-hero__id">{overview.id}</span>
              </div>
              <dl className="detail-list">
                <Detail label="Status" value={formatLabel(overview.status)} />
                <Detail
                  label="Created by"
                  value={overview.creator.display_name}
                />
                <Detail
                  label="Publication"
                  value={formatLabel(overview.lifecycle.publication_state)}
                />
                <Detail
                  label="Discovery"
                  value={formatLabel(overview.lifecycle.discovery_status)}
                />
                <Detail
                  label="Review"
                  value={formatReview(overview.lifecycle)}
                />
                <Detail
                  label="Last updated"
                  value={formatDate(overview.updated_at)}
                />
              </dl>
            </section>

            <section className="overview-panel" aria-labelledby="data-sources-title">
              <div className="overview-panel__header">
                <div>
                  <p className="section-eyebrow">Connections</p>
                  <h2 id="data-sources-title">Data sources</h2>
                </div>
                <span className="count-badge">{overview.data_sources.length}</span>
              </div>
              {overview.data_sources.length ? (
                <ul className="data-source-list">
                  {overview.data_sources.map((source) => (
                    <li key={source.data_source_id}>
                      <div>
                        <strong>{source.name}</strong>
                        <span>{source.data_source_id}</span>
                      </div>
                      <div className="data-source-list__meta">
                        <span>{source.connector || "Connector unavailable"}</span>
                        <span>
                          {source.selected_assets.length
                            ? `${source.selected_assets.length} selected assets`
                            : "All authorized assets"}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="overview-panel__empty">
                  No data sources were returned by the API.
                </p>
              )}
            </section>
          </div>
        </>
      ) : (
        <ErrorState
          title="Model overview is unavailable"
          message="The API did not return overview data for this model."
          onRetry={context.retry}
        />
      )}
    </>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <article className="summary-card summary-card--compact">
      <p>{label}</p>
      <strong>{value}</strong>
      <span>Visible to your account</span>
    </article>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function formatLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function formatReview(
  lifecycle: NonNullable<ApplicationContextState["modelOverview"]>["lifecycle"],
): string {
  if (lifecycle.unresolved_review_items !== null) {
    return lifecycle.unresolved_review_items === 1
      ? "1 unresolved item"
      : `${lifecycle.unresolved_review_items} unresolved items`;
  }
  return formatLabel(lifecycle.review_status);
}
