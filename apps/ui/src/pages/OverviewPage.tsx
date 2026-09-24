import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import {
  HealthComponent,
  HealthState,
  ModelSystemStatus,
} from "../api/client";
import { EmptyState, ErrorState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface OverviewPageProps {
  context: ApplicationContextState;
}

export default function OverviewPage({ context }: OverviewPageProps) {
  const location = useLocation();
  const [publishState, setPublishState] = useState<
    "idle" | "saving" | "success" | "error"
  >("idle");
  const [publishMessage, setPublishMessage] = useState("");
  const [systemStatus, setSystemStatus] = useState<ModelSystemStatus>();
  const [systemStatusState, setSystemStatusState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [systemStatusError, setSystemStatusError] = useState("");
  const [statusRetryVersion, setStatusRetryVersion] = useState(0);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [detailsState, setDetailsState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [detailsError, setDetailsError] = useState("");
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
  const reviewRunId = overview?.lifecycle.latest_run_id;
  const reviewSearch = new URLSearchParams(location.search);
  if (context.selectedOrganizationId) {
    reviewSearch.set("organization", context.selectedOrganizationId);
  }
  if (context.selectedModelId) {
    reviewSearch.set("model", context.selectedModelId);
  }
  if (reviewRunId) reviewSearch.set("review_run_id", reviewRunId);

  useEffect(() => {
    if (!context.selectedModelId || context.overviewStatus !== "ready") {
      setSystemStatus(undefined);
      setSystemStatusState("idle");
      return;
    }
    let active = true;
    setSystemStatusState("loading");
    setSystemStatusError("");
    setDetailsExpanded(false);
    setDetailsState("idle");
    void context
      .loadModelStatus(context.selectedModelId)
      .then((result) => {
        if (!active) return;
        setSystemStatus(result);
        setSystemStatusState("ready");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setSystemStatus(undefined);
        setSystemStatusState("error");
        setSystemStatusError(
          error instanceof Error
            ? error.message
            : "System status is unavailable.",
        );
      });
    return () => {
      active = false;
    };
  }, [
    context.loadModelStatus,
    context.overviewStatus,
    context.selectedModelId,
    statusRetryVersion,
  ]);

  async function loadSystemDetails() {
    setDetailsState("loading");
    setDetailsError("");
    try {
      const result = await context.loadModelStatus(
        context.selectedModelId,
        true,
      );
      setSystemStatus(result);
      setDetailsState("ready");
    } catch (error) {
      setDetailsState("error");
      setDetailsError(
        error instanceof Error
          ? error.message
          : "Detailed system status is unavailable.",
      );
    }
  }

  function toggleSystemDetails() {
    if (!systemStatus?.details_available) return;
    if (detailsExpanded) {
      setDetailsExpanded(false);
      return;
    }
    setDetailsExpanded(true);
    if (detailsState !== "ready") void loadSystemDetails();
  }

  async function publish() {
    if (!overview || !reviewRunId) return;
    setPublishState("saving");
    setPublishMessage("");
    try {
      await context.publishModelReview(overview.id, reviewRunId);
      setPublishState("success");
      setPublishMessage("Model published successfully.");
      context.refreshModelOverview();
    } catch (error) {
      setPublishState("error");
      setPublishMessage(
        error instanceof Error ? error.message : "Publishing failed.",
      );
    }
  }

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
              {reviewRunId &&
              actions.has("model.edit") &&
              overview.lifecycle.review_status === "pending" ? (
                <Link
                  className="button button--secondary"
                  to={{
                    pathname: "/canvas",
                    search: `?${reviewSearch.toString()}`,
                  }}
                >
                  Review Proposals
                </Link>
              ) : null}
              {reviewRunId &&
              actions.has("model.publish") &&
              overview.lifecycle.review_status === "complete" ? (
                <button
                  className="button button--secondary"
                  disabled={publishState === "saving"}
                  onClick={() => void publish()}
                  type="button"
                >
                  {publishState === "saving" ? "Publishing…" : "Publish"}
                </button>
              ) : null}
            </div>
            {publishMessage ? (
              <p
                className={`action-message action-message--${publishState}`}
                role={publishState === "error" ? "alert" : "status"}
              >
                {publishMessage}
              </p>
            ) : null}
          </section>

          <SystemStatusSection
            status={systemStatus}
            search={location.search}
            loadState={systemStatusState}
            error={systemStatusError}
            detailsExpanded={detailsExpanded}
            detailsState={detailsState}
            detailsError={detailsError}
            onToggleDetails={toggleSystemDetails}
            onRetryDetails={() => void loadSystemDetails()}
            onRetry={() =>
              setStatusRetryVersion((version) => version + 1)
            }
          />

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

function SystemStatusSection({
  status,
  search,
  loadState,
  error,
  detailsExpanded,
  detailsState,
  detailsError,
  onToggleDetails,
  onRetryDetails,
  onRetry,
}: {
  status?: ModelSystemStatus;
  search: string;
  loadState: "idle" | "loading" | "ready" | "error";
  error: string;
  detailsExpanded: boolean;
  detailsState: "idle" | "loading" | "ready" | "error";
  detailsError: string;
  onToggleDetails: () => void;
  onRetryDetails: () => void;
  onRetry: () => void;
}) {
  return (
    <section className="system-status" aria-labelledby="system-status-title">
      <div className="system-status__header">
        <div>
          <p className="section-eyebrow">System status</p>
          <h2 id="system-status-title">Helios health</h2>
        </div>
        {status ? <StatusBadge status={status.status} /> : null}
      </div>

      {loadState === "loading" || loadState === "idle" ? (
        <div className="system-status__loading" role="status">
          <span className="spinner" aria-hidden="true" />
          Checking model services…
        </div>
      ) : loadState === "error" ? (
        <div className="system-status__error" role="alert">
          <span>{error || "System status is unavailable."}</span>
          <button
            className="button button--secondary"
            onClick={onRetry}
            type="button"
          >
            Retry status
          </button>
        </div>
      ) : status ? (
        <>
          <StatusComponents components={status.components} />
          <div className="system-status__footer">
            <RecentActivity status={status} search={search} />
            <button
              className="button button--secondary"
              onClick={onRetry}
              type="button"
            >
              Refresh status
            </button>
            {status.details_available ? (
              <button
                aria-expanded={detailsExpanded}
                className="button button--secondary"
                onClick={onToggleDetails}
                type="button"
              >
                {detailsExpanded
                  ? "Hide system details"
                  : "Show system details"}
              </button>
            ) : null}
          </div>
          {detailsExpanded ? (
            <div className="system-status__details">
              <h3>Infrastructure checks</h3>
              {detailsState === "loading" ? (
                <p role="status">Running detailed checks…</p>
              ) : detailsState === "error" ? (
                <div role="alert">
                  <span>
                    {detailsError ||
                      "Detailed system status is unavailable."}
                  </span>
                  <button
                    className="button button--secondary"
                    onClick={onRetryDetails}
                    type="button"
                  >
                    Retry details
                  </button>
                </div>
              ) : (
                <StatusComponents components={status.details} />
              )}
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function StatusComponents({
  components,
}: {
  components: HealthComponent[];
}) {
  return (
    <ul className="system-status__components">
      {components.map((component) => (
        <li key={component.id}>
          <div>
            <strong>{component.label}</strong>
            <span>{component.description}</span>
          </div>
          <StatusBadge status={component.status} />
        </li>
      ))}
    </ul>
  );
}

function StatusBadge({ status }: { status: HealthState }) {
  return (
    <span className={`health-badge health-badge--${status}`}>
      {formatLabel(status)}
    </span>
  );
}

function RecentActivity({
  status,
  search,
}: {
  status: ModelSystemStatus;
  search: string;
}) {
  const discovery = status.recent_activity.discovery;
  const profile = status.recent_activity.profile;
  if (!discovery && !profile) {
    return <span>No successful discovery or profile run recorded.</span>;
  }
  const recent = profile ?? discovery;
  const label = profile ? "profile" : "discovery";
  return (
    <span>
      Last {label}{" "}
      <Link
        className="text-link"
        to={{
          pathname: `/models/runs/${encodeURIComponent(recent!.run_id)}`,
          search,
        }}
      >
        {formatOptionalDate(recent!.completed_at)}
      </Link>
    </span>
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

function formatOptionalDate(value: string | null): string {
  return value ? formatDate(value) : "completed (time unavailable)";
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
