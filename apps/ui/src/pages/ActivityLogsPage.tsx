import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ApiUnavailableError,
  AuditEvent,
  AuditEventCollection,
  AuditSession,
  AuthenticationError,
  AuthorizationError,
} from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface ActivityLogsPageProps {
  context: ApplicationContextState;
}

const PAGE_SIZE = 50;

function errorMessage(error: unknown): string {
  if (error instanceof AuthenticationError) {
    return "Your Helios session has expired. Sign in again to view activity.";
  }
  if (error instanceof AuthorizationError) {
    return "You do not have permission to view the requested activity.";
  }
  if (error instanceof ApiUnavailableError) return error.message;
  return "Helios could not load activity logs.";
}

function localTime(value: string): string {
  return new Date(value).toLocaleString();
}

export default function ActivityLogsPage({
  context,
}: ActivityLogsPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [collection, setCollection] = useState<AuditEventCollection>();
  const [sessions, setSessions] = useState<AuditSession[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent>();
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [version, setVersion] = useState(0);

  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const sessionId = searchParams.get("session") ?? "";
  const component = searchParams.get("component") ?? "";
  const outcome = searchParams.get("outcome") ?? "";
  const severity = searchParams.get("severity") ?? "";
  const principalId = searchParams.get("principal") ?? "";
  const includeAll = searchParams.get("scope") === "organization";
  const eventId = searchParams.get("event") ?? "";
  const organizationId = context.selectedOrganizationId || undefined;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(undefined);
    Promise.all([
      context.loadAuditEvents({
        organizationId,
        principalId: includeAll && principalId ? principalId : undefined,
        sessionId: sessionId || undefined,
        component: component || undefined,
        outcome: outcome || undefined,
        severity: severity || undefined,
        includeAll,
        offset: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
      }),
      context.loadAuditSessions(organizationId, includeAll),
    ])
      .then(([events, sessionCollection]) => {
        if (cancelled) return;
        setCollection(events);
        setSessions(sessionCollection.sessions);
      })
      .catch((loadError) => {
        if (!cancelled) setError(errorMessage(loadError));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [
    component,
    context.loadAuditEvents,
    context.loadAuditSessions,
    includeAll,
    organizationId,
    outcome,
    page,
    principalId,
    sessionId,
    severity,
    version,
  ]);

  useEffect(() => {
    let cancelled = false;
    if (!eventId) {
      setSelectedEvent(undefined);
      return () => { cancelled = true; };
    }
    setDetailLoading(true);
    void context.loadAuditEvent(eventId)
      .then((event) => {
        if (!cancelled) setSelectedEvent(event);
      })
      .catch((loadError) => {
        if (!cancelled) setError(errorMessage(loadError));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => { cancelled = true; };
  }, [context.loadAuditEvent, eventId]);

  const availableActions = new Set(collection?.available_actions ?? []);
  const canViewOrganization = availableActions.has(
    "audit.read_organization",
  );
  const totalPages = Math.max(
    1,
    Math.ceil((collection?.page.total ?? 0) / PAGE_SIZE),
  );
  const principals = useMemo(
    () => Array.from(
      new Set(
        (collection?.items ?? [])
          .map((item) => item.principal_id)
          .filter((item): item is string => Boolean(item)),
      ),
    ).sort(),
    [collection],
  );

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(changes).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    setSearchParams(next);
  };

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    update({ page: undefined });
  };

  if (loading && !collection) {
    return <LoadingState label="Loading activity logs…" />;
  }
  if (error && !collection) {
    return (
      <ErrorState
        title="Activity logs unavailable"
        message={error}
        onRetry={() => setVersion((value) => value + 1)}
      />
    );
  }

  return (
    <div className="activity-page">
      <header className="activity-header">
        <div>
          <p className="section-eyebrow">Audit trail</p>
          <h1>Activity Logs</h1>
          <p>
            Review your Helios sessions and redacted server-side actions.
          </p>
        </div>
        <button
          className="button button--secondary"
          type="button"
          onClick={() => setVersion((value) => value + 1)}
        >
          Refresh
        </button>
      </header>

      <form className="activity-filters" onSubmit={applyFilters}>
        <label>
          Session
          <select
            value={sessionId}
            onChange={(event) =>
              update({ session: event.target.value, page: undefined })
            }
          >
            <option value="">All sessions</option>
            {sessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {localTime(session.last_seen_at)} · {session.event_count} events
              </option>
            ))}
          </select>
        </label>
        <label>
          Component
          <select
            value={component}
            onChange={(event) =>
              update({ component: event.target.value, page: undefined })
            }
          >
            <option value="">All components</option>
            <option value="api">API</option>
            <option value="ui">UI</option>
            <option value="mcp">MCP</option>
            <option value="job">Jobs</option>
          </select>
        </label>
        <label>
          Outcome
          <select
            value={outcome}
            onChange={(event) =>
              update({ outcome: event.target.value, page: undefined })
            }
          >
            <option value="">All outcomes</option>
            <option value="success">Success</option>
            <option value="denied">Denied</option>
            <option value="error">Error</option>
            <option value="started">Started</option>
          </select>
        </label>
        <label>
          Severity
          <select
            value={severity}
            onChange={(event) =>
              update({ severity: event.target.value, page: undefined })
            }
          >
            <option value="">All severities</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select>
        </label>
        {canViewOrganization ? (
          <>
            <label className="activity-filters__scope">
              <input
                type="checkbox"
                checked={includeAll}
                onChange={(event) =>
                  update({
                    scope: event.target.checked ? "organization" : undefined,
                    principal: undefined,
                    page: undefined,
                  })
                }
              />
              Organization activity
            </label>
            {includeAll ? (
              <label>
                Principal
                <input
                  value={principalId}
                  list="activity-principals"
                  placeholder="All principals"
                  onChange={(event) =>
                    update({
                      principal: event.target.value,
                      page: undefined,
                    })
                  }
                />
                <datalist id="activity-principals">
                  {principals.map((principal) => (
                    <option key={principal} value={principal} />
                  ))}
                </datalist>
              </label>
            ) : null}
          </>
        ) : null}
      </form>

      {error ? (
        <div className="inline-message inline-message--error" role="alert">
          {error}
        </div>
      ) : null}

      {!collection?.items.length ? (
        <EmptyState
          title="No activity found"
          message="No persisted audit events match the selected filters."
        />
      ) : (
        <>
          <div className="activity-table-wrap">
            <table className="activity-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Component</th>
                  <th scope="col">Action</th>
                  <th scope="col">Outcome</th>
                  {includeAll ? <th scope="col">Principal</th> : null}
                  <th scope="col">Summary</th>
                </tr>
              </thead>
              <tbody>
                {collection.items.map((item) => (
                  <tr key={item.id}>
                    <td>{localTime(item.occurred_at)}</td>
                    <td>{item.component.toUpperCase()}</td>
                    <td>
                      <button
                        className="button-link"
                        type="button"
                        onClick={() => update({ event: item.id })}
                      >
                        {item.action}
                      </button>
                    </td>
                    <td>
                      <span className={`status-pill status-pill--${item.outcome}`}>
                        {item.outcome}
                      </span>
                    </td>
                    {includeAll ? <td>{item.principal_id ?? "System"}</td> : null}
                    <td>{item.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <nav className="pagination" aria-label="Activity pages">
            <button
              className="button button--secondary"
              type="button"
              disabled={page <= 1}
              onClick={() => update({ page: String(page - 1) })}
            >
              Previous
            </button>
            <span>Page {page} of {totalPages}</span>
            <button
              className="button button--secondary"
              type="button"
              disabled={!collection.page.has_more}
              onClick={() => update({ page: String(page + 1) })}
            >
              Next
            </button>
          </nav>
        </>
      )}

      {eventId ? (
        <aside className="activity-detail" aria-label="Activity event detail">
          {detailLoading || !selectedEvent ? (
            <LoadingState label="Loading event detail…" />
          ) : (
            <>
              <div className="activity-detail__header">
                <div>
                  <p className="section-eyebrow">Event detail</p>
                  <h2>{selectedEvent.action}</h2>
                </div>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => update({ event: undefined })}
                >
                  Close
                </button>
              </div>
              <dl className="detail-list">
                <div><dt>Occurred</dt><dd>{localTime(selectedEvent.occurred_at)}</dd></div>
                <div><dt>Outcome</dt><dd>{selectedEvent.outcome}</dd></div>
                <div><dt>Component</dt><dd>{selectedEvent.component}</dd></div>
                <div><dt>Principal</dt><dd>{selectedEvent.principal_id ?? "System"}</dd></div>
                <div><dt>Session</dt><dd>{selectedEvent.session_id ?? "Not available"}</dd></div>
                <div><dt>Request</dt><dd>{selectedEvent.request_id ?? "Not available"}</dd></div>
                <div><dt>Resource</dt><dd>{selectedEvent.resource_id ?? "Not available"}</dd></div>
                <div><dt>HTTP status</dt><dd>{selectedEvent.http_status ?? "Not applicable"}</dd></div>
                <div><dt>Duration</dt><dd>{selectedEvent.duration_ms == null ? "Not available" : `${selectedEvent.duration_ms.toFixed(1)} ms`}</dd></div>
              </dl>
              {Object.keys(selectedEvent.details).length ? (
                <details>
                  <summary>Sanitized details</summary>
                  <pre>{JSON.stringify(selectedEvent.details, null, 2)}</pre>
                </details>
              ) : null}
              {selectedEvent.diagnostics ? (
                <details>
                  <summary>Administrator diagnostics</summary>
                  <p>
                    Detailed exception information is restricted to organization
                    administrators. Credentials, tokens, and generated SQL are
                    redacted.
                  </p>
                  <pre>
                    {JSON.stringify(selectedEvent.diagnostics, null, 2)}
                  </pre>
                </details>
              ) : null}
            </>
          )}
        </aside>
      ) : null}
    </div>
  );
}
