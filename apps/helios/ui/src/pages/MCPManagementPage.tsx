import { FormEvent, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { MCPSettings, MCPStatus } from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";
import EvaluationWorkspace from "../features/trace/EvaluationWorkspace";
import TraceWorkspace from "../features/trace/TraceWorkspace";

interface MCPManagementPageProps {
  context: ApplicationContextState;
}

export default function MCPManagementPage({
  context,
}: MCPManagementPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [settings, setSettings] = useState<MCPSettings>();
  const [status, setStatus] = useState<MCPStatus>();
  const [maxToolRounds, setMaxToolRounds] = useState(6);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [statusLoading, setStatusLoading] = useState(false);
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "success" | "error"
  >("idle");
  const [message, setMessage] = useState("");
  const requestedTab = searchParams.get("tab");
  const tab = requestedTab === "traces" || requestedTab === "evaluations"
    ? requestedTab
    : "connection";
  const selectedModel = context.models.find(
    (model) => model.id === context.selectedModelId,
  );
  const evaluationAvailable = selectedModel?.available_actions.includes(
    "organization.manage",
  ) ?? false;

  function selectTab(nextTab: "connection" | "traces" | "evaluations") {
    const next = new URLSearchParams(searchParams);
    next.set("tab", nextTab);
    setSearchParams(next, { replace: true });
  }

  const refreshStatus = useCallback(async () => {
    if (!context.selectedModelId) return;
    setStatusLoading(true);
    try {
      setStatus(await context.loadMcpStatus(context.selectedModelId));
    } catch (error) {
      setStatus({
        status: "unavailable",
        checked_at: new Date().toISOString(),
        message: error instanceof Error
          ? error.message
          : "MCP status could not be loaded.",
        timeout_seconds: null,
        server: {},
        tools: [],
      });
    } finally {
      setStatusLoading(false);
    }
  }, [context.loadMcpStatus, context.selectedModelId]);

  useEffect(() => {
    let active = true;
    void context.loadMcpSettings()
      .then((loaded) => {
        if (!active) return;
        setSettings(loaded);
        setMaxToolRounds(loaded.max_tool_rounds);
        setLoadState("ready");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setMessage(
          error instanceof Error
            ? error.message
            : "MCP settings could not be loaded.",
        );
        setLoadState("error");
      });
    return () => {
      active = false;
    };
  }, [context.loadMcpSettings]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaveState("saving");
    setMessage("");
    try {
      const updated = await context.updateMcpSettings(maxToolRounds);
      setSettings(updated);
      setMaxToolRounds(updated.max_tool_rounds);
      setSaveState("success");
      setMessage(
        "Session override saved. New Talk requests will use this limit.",
      );
    } catch (error) {
      setSaveState("error");
      setMessage(
        error instanceof Error ? error.message : "MCP settings could not be saved.",
      );
    }
  }

  async function useDefault() {
    setSaveState("saving");
    setMessage("");
    try {
      const updated = await context.deleteMcpSettings();
      setSettings(updated);
      setMaxToolRounds(updated.max_tool_rounds);
      setSaveState("success");
      setMessage("The project MCP limit is active.");
    } catch (error) {
      setSaveState("error");
      setMessage(
        error instanceof Error
          ? error.message
          : "The MCP session override could not be cleared.",
      );
    }
  }

  if (loadState === "loading") {
    return <LoadingState label="Loading MCP management…" />;
  }
  if (loadState === "error" || !settings) {
    return <ErrorState title="MCP settings unavailable" message={message} />;
  }

  const serverLabel = [
    status?.server.server_name,
    status?.server.server_version,
  ].filter(Boolean).join(" · ");

  return (
    <div className={`mcp-management${
      tab === "traces" ? " mcp-management--traces" : ""
    }`}>
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">Settings</p>
          <h1>MCP Server</h1>
          <p>
            Monitor the Helios MCP service and control the bounded tool loop
            used by Talk to Your Data.
          </p>
        </div>
        <span className={`provider-settings__source provider-settings__source--${
          settings.source
        }`}>
          {settings.source === "session" ? "Session override" : "Project default"}
        </span>
      </header>

      <nav className="mcp-management__tabs" aria-label="MCP management sections">
        <button
          type="button"
          aria-pressed={tab === "connection"}
          onClick={() => selectTab("connection")}
        >
          Connection
        </button>
        <button
          type="button"
          aria-pressed={tab === "traces"}
          onClick={() => selectTab("traces")}
        >
          Traces
        </button>
        {evaluationAvailable ? (
          <button
            type="button"
            aria-pressed={tab === "evaluations"}
            onClick={() => selectTab("evaluations")}
          >
            Evaluations
          </button>
        ) : null}
      </nav>

      {tab === "connection" ? (
        <>
      <section className="mcp-management__status" aria-labelledby="mcp-status-title">
        <div>
          <p className="page-header__eyebrow">Connection</p>
          <h2 id="mcp-status-title">MCP server</h2>
          {statusLoading && !status ? (
            <p role="status">Checking MCP service…</p>
          ) : (
            <>
              <p className={`mcp-management__availability mcp-management__availability--${
                status?.status ?? "unavailable"
              }`}>
                <span aria-hidden="true" />
                {status?.status === "available" ? "Available" : "Unavailable"}
              </p>
              <p>{status?.message ?? "MCP status has not been checked."}</p>
              {serverLabel ? <p className="mcp-management__metadata">{serverLabel}</p> : null}
              {status?.server.protocol_version ? (
                <p className="mcp-management__metadata">
                  Protocol {status.server.protocol_version}
                </p>
              ) : null}
            </>
          )}
        </div>
        <button
          className="button button--secondary"
          type="button"
          disabled={statusLoading || !context.selectedModelId}
          onClick={() => void refreshStatus()}
        >
          {statusLoading ? "Checking…" : "Check connection"}
        </button>
      </section>

      <div className="mcp-management__grid">
        <form className="provider-settings__form" onSubmit={save}>
          <div>
            <h2>Conversation tool limit</h2>
            <p>
              Each round lets the LLM request one or more MCP tools before it
              receives their results. Helios stops if no final answer is
              produced within this many rounds.
            </p>
          </div>
          <label>
            <span>Maximum tool-call rounds</span>
            <input
              aria-label="Maximum tool-call rounds"
              type="number"
              min={settings.limits.min_tool_rounds}
              max={settings.limits.max_tool_rounds}
              required
              value={maxToolRounds}
              onChange={(event) => {
                setMaxToolRounds(event.currentTarget.valueAsNumber);
                setSaveState("idle");
              }}
            />
            <small>
              Allowed range: {settings.limits.min_tool_rounds}–{
                settings.limits.max_tool_rounds
              }. Higher values can increase latency, LLM cost, and database
              query activity.
            </small>
          </label>
          {message ? (
            <p
              className={`action-message action-message--${saveState}`}
              role={saveState === "error" ? "alert" : "status"}
            >
              {message}
            </p>
          ) : null}
          <div className="provider-settings__actions">
            <button
              className="button button--primary"
              type="submit"
              disabled={
                saveState === "saving"
                || !Number.isInteger(maxToolRounds)
                || maxToolRounds < settings.limits.min_tool_rounds
                || maxToolRounds > settings.limits.max_tool_rounds
              }
            >
              {saveState === "saving" ? "Saving…" : "Use for this session"}
            </button>
            <button
              className="button button--secondary"
              type="button"
              disabled={saveState === "saving" || settings.source !== "session"}
              onClick={() => void useDefault()}
            >
              Use project default ({settings.default_max_tool_rounds})
            </button>
          </div>
        </form>

        <section className="mcp-management__runtime" aria-labelledby="runtime-title">
          <h2 id="runtime-title">Runtime controls</h2>
          <dl>
            <div>
              <dt>Conversation timeout</dt>
              <dd>
                {status?.timeout_seconds != null
                  ? `${status.timeout_seconds} seconds`
                  : "Unavailable"}
              </dd>
            </div>
            <div>
              <dt>Tool authorization</dt>
              <dd>Signed-in user and selected semantic model</dd>
            </div>
            <div>
              <dt>Health probe timeout</dt>
              <dd>10 seconds</dd>
            </div>
          </dl>
          <p>
            Connection credentials, endpoint selection, and timeout policy
            remain deployment-level settings so browser sessions cannot weaken
            service security controls.
          </p>
        </section>
      </div>

      <section className="mcp-management__tools" aria-labelledby="mcp-tools-title">
        <div className="mcp-management__tools-heading">
          <div>
            <p className="page-header__eyebrow">Capabilities</p>
            <h2 id="mcp-tools-title">Available tools</h2>
          </div>
          <span>{status?.tools.length ?? 0} tools</span>
        </div>
        {status?.tools.length ? (
          <ul>
            {status.tools.map((tool) => (
              <li key={tool.name}>
                <code>{tool.name}</code>
                <p>{tool.description || "No description provided by the MCP server."}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p>
            Tool discovery is available when the MCP service is connected for
            the selected semantic model.
          </p>
        )}
      </section>
        </>
      ) : tab === "traces" ? (
        <TraceWorkspace context={context} />
      ) : evaluationAvailable ? (
        <EvaluationWorkspace context={context} />
      ) : (
        <p className="action-message action-message--error">
          Organization administration permission is required.
        </p>
      )}
    </div>
  );
}
