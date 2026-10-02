import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  AssistantJourney,
  AssistantJourneyStep,
  AssistantWorkspaceState,
} from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { mergeContextParams } from "../components/AssistantDrawer";
import { ApplicationContextState } from "../hooks/useApplicationContext";
import "./HomePage.css";

interface HomePageProps {
  context: ApplicationContextState;
  onOpenAssistant?: () => void;
}

function formatLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function StepMarker({ status }: { status: AssistantJourneyStep["status"] }) {
  const label =
    status === "done" ? "Done" : status === "external" ? "Outside Helios" : "To do";
  return (
    <span
      className={`journey-step__marker journey-step__marker--${status}`}
      role="img"
      aria-label={label}
      title={label}
    >
      {status === "done" ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m5 12 5 5 9-10" />
        </svg>
      ) : status === "external" ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 4 3 20h18zM12 10v5m0 2v.5" />
        </svg>
      ) : null}
    </span>
  );
}

function JourneyPanel({
  journey,
  context,
}: {
  journey: AssistantJourney;
  context: ApplicationContextState;
}) {
  return (
    <section
      className="journey-panel"
      aria-labelledby={`journey-${journey.id}`}
    >
      <header className="journey-panel__header">
        <div>
          <h2 id={`journey-${journey.id}`}>{journey.title}</h2>
          <p>{journey.description}</p>
        </div>
        <span className="count-badge">
          {journey.progress.done} of {journey.progress.total}
        </span>
      </header>
      <ol className="journey-steps">
        {journey.steps.map((step) => {
          const isNext = step.id === journey.next_step_id;
          return (
            <li
              className={`journey-step journey-step--${step.status}${
                isNext ? " journey-step--next" : ""
              }`}
              key={step.id}
            >
              <StepMarker status={step.status} />
              <div className="journey-step__body">
                <div className="journey-step__title">
                  <strong>{step.title}</strong>
                  {isNext ? (
                    <span className="journey-step__next">Next</span>
                  ) : null}
                </div>
                <p>{step.description}</p>
                {step.status === "external" && step.note ? (
                  <p className="journey-step__note">{step.note}</p>
                ) : null}
              </div>
              <div className="journey-step__links">
                <Link
                  className="button button--secondary journey-step__open"
                  to={{
                    pathname: step.route,
                    search: mergeContextParams(context, step.params),
                  }}
                  aria-label={`Open ${step.title}`}
                >
                  Open
                </Link>
                {step.doc_slug ? (
                  <Link
                    className="journey-step__guide"
                    to={`/docs/helios/${step.doc_slug}`}
                    aria-label={`Read guide for ${step.title}`}
                  >
                    Read guide
                  </Link>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function HomePage({ context, onOpenAssistant }: HomePageProps) {
  const [state, setState] = useState<AssistantWorkspaceState>();
  const [loadState, setLoadState] = useState<
    "idle" | "loading" | "ready" | "error" | "unsupported"
  >("idle");
  const [error, setError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const organizationId = context.selectedOrganizationId;
  const modelId = context.selectedModelId;
  const organization = context.organizations.find(
    (item) => item.id === organizationId,
  );

  useEffect(() => {
    if (context.status !== "ready" || !organizationId) return;
    let cancelled = false;
    setLoadState("loading");
    let request: Promise<AssistantWorkspaceState>;
    try {
      // The context wrapper throws synchronously when the API client predates
      // the assistant endpoints; network failures reject asynchronously.
      request = context.loadAssistantWorkspaceState(
        organizationId,
        modelId || undefined,
      );
    } catch {
      setLoadState("unsupported");
      return;
    }
    request
      .then((result) => {
        if (cancelled) return;
        setState(result);
        setLoadState("ready");
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof Error
            ? cause.message
            : "The workspace state could not be loaded.",
        );
        setLoadState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [
    context.loadAssistantWorkspaceState,
    context.status,
    modelId,
    organizationId,
    retryVersion,
  ]);

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

  return (
    <div className="home-page">
      <header className="page-header">
        <div>
          <h1>{organization?.name ?? "Helios workspace"}</h1>
        </div>
        {onOpenAssistant ? (
          <button
            className="button button--primary home-page__assistant"
            type="button"
            onClick={onOpenAssistant}
          >
            Ask the assistant
          </button>
        ) : null}
      </header>

      {loadState === "unsupported" ? (
        <EmptyState
          title="Workspace state unavailable"
          message="This Helios API does not expose workspace state, so the Home checklist cannot be shown."
        />
      ) : loadState === "loading" || loadState === "idle" ? (
        <LoadingState label="Loading your workspace…" />
      ) : loadState === "error" ? (
        <ErrorState
          title="Workspace state could not be loaded"
          message={error}
          onRetry={() => setRetryVersion((version) => version + 1)}
        />
      ) : state ? (
        <>
          <section
            className="summary-grid summary-grid--four"
            aria-label="Workspace status"
          >
            <article className="summary-card summary-card--compact">
              <p>Models</p>
              <strong>{state.models.length}</strong>
              <span>Semantic models in this organization</span>
            </article>
            <article className="summary-card summary-card--compact">
              <p>Data sources</p>
              <strong>{state.data_sources.length}</strong>
              <span>
                {state.crawl_runs.total} crawl run
                {state.crawl_runs.total === 1 ? "" : "s"}
              </span>
            </article>
            <article className="summary-card summary-card--compact">
              <p>Ontology</p>
              <strong className="summary-card__text">
                {state.ontology.active_version ?? "None active"}
              </strong>
              <span>
                {state.ontology.version_count} version
                {state.ontology.version_count === 1 ? "" : "s"} published
              </span>
            </article>
            <article className="summary-card summary-card--compact">
              <p>LLM provider</p>
              <strong className="summary-card__text">
                {state.llm_provider.configured
                  ? `Connected · ${state.llm_provider.provider ?? "provider"}`
                  : "Not configured"}
              </strong>
              <span>
                {state.llm_provider.configured ? (
                  state.llm_provider.model ?? "Ready for Talk to Your Data"
                ) : (
                  <Link
                    to={{
                      pathname: "/governance/model-provider",
                      search: mergeContextParams(context, {}),
                    }}
                  >
                    Configure an LLM provider
                  </Link>
                )}
              </span>
            </article>
          </section>

          <div className="journey-grid">
            {state.journeys.map((journey) => (
              <JourneyPanel journey={journey} context={context} key={journey.id} />
            ))}
          </div>

          <section className="home-models" aria-labelledby="home-models-heading">
            <h2 id="home-models-heading">Models</h2>
            {state.models.length === 0 ? (
              <p className="home-models__empty">
                No semantic models yet. Follow the journey above to create one.
              </p>
            ) : (
              <ul className="home-models__list">
                {state.models.map((model) => (
                  <li className="home-models__row" key={model.id}>
                    <span className="home-models__name">{model.name}</span>
                    <span
                      className={`lifecycle-badge lifecycle-badge--${model.lifecycle.publication_state}`}
                    >
                      {formatLabel(model.lifecycle.publication_state)}
                    </span>
                    <span className="home-models__unresolved">
                      {model.lifecycle.unresolved_review_items === null
                        ? "Review not available"
                        : `${model.lifecycle.unresolved_review_items} unresolved`}
                    </span>
                    <Link
                      className="home-models__link"
                      to={`/model-overview?${new URLSearchParams({
                        organization: state.organization.id,
                        model: model.id,
                      }).toString()}`}
                      aria-label={`Open ${model.name} overview`}
                    >
                      Open overview
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
