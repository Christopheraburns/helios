import {
  FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  ApiUnavailableError,
  AuthenticationError,
  AuthorizationError,
  ConflictError,
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
} from "../api/client";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "../components/AsyncState";
import {
  EntityClaims,
  EntityClaimsResult,
  EvidenceSearch,
  EvidenceSearchResult,
} from "../components/EvidenceResults";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface TalkPageProps {
  context: ApplicationContextState;
}

const RESULT_PAGE_SIZE = 50;
const WORKING_MESSAGES = [
  "Searching governed datastores…",
  "Reviewing the semantic model…",
  "Planning a secure query…",
  "Waiting for the data platform…",
  "Interpreting query results…",
  "Working on your answer…",
] as const;

function conversationError(error: unknown): string {
  if (error instanceof AuthenticationError) {
    return "Your Helios session has expired. Sign in again and retry.";
  }
  if (error instanceof AuthorizationError) {
    return "You no longer have permission to use this model.";
  }
  if (error instanceof ApiUnavailableError) {
    return error.message;
  }
  return "Helios could not complete this conversation request.";
}

function summary(conversation: ConversationDetail): ConversationSummary {
  const { messages: _messages, ...item } = conversation;
  return item;
}

function conversationTurns(
  conversation: ConversationDetail,
): Record<string, ConversationTurn> {
  return Object.fromEntries(
    conversation.messages
      .filter(
        (message): message is typeof message & { turn: ConversationTurn } =>
          message.role === "assistant" && Boolean(message.turn),
      )
      .map((message) => [message.id, message.turn]),
  );
}

function QueryResult({ turn }: { turn: ConversationTurn }) {
  const [page, setPage] = useState(0);
  const result = turn.query_result;
  if (!result) return null;
  const pageCount = Math.max(
    1,
    Math.ceil(result.rows.length / RESULT_PAGE_SIZE),
  );
  const currentPage = Math.min(page, pageCount - 1);
  const rows = result.rows.slice(
    currentPage * RESULT_PAGE_SIZE,
    (currentPage + 1) * RESULT_PAGE_SIZE,
  );
  return (
    <section className="talk-result" aria-label="Query result">
      <div className="talk-result__header">
        <h4>Result</h4>
        <span>
          {result.rows.length.toLocaleString()} row
          {result.rows.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="talk-result__table-wrap">
        <table className="talk-result__table">
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th key={column} scope="col">{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${currentPage}-${rowIndex}`}>
                {result.columns.map((column, columnIndex) => (
                  <td key={`${column}-${columnIndex}`}>
                    {row[columnIndex] == null
                      ? "—"
                      : String(row[columnIndex])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pageCount > 1 ? (
        <div className="talk-result__pagination" aria-label="Result pages">
          <button
            className="button button--secondary"
            type="button"
            disabled={currentPage === 0}
            onClick={() => setPage((value) => Math.max(0, value - 1))}
          >
            Previous
          </button>
          <span>Page {currentPage + 1} of {pageCount}</span>
          <button
            className="button button--secondary"
            type="button"
            disabled={currentPage + 1 === pageCount}
            onClick={() =>
              setPage((value) => Math.min(pageCount - 1, value + 1))
            }
          >
            Next
          </button>
        </div>
      ) : null}
      {result.sql ? (
        <details className="talk-details">
          <summary>View generated SQL</summary>
          <pre>{result.sql}</pre>
        </details>
      ) : null}
    </section>
  );
}

function DocumentResults({ turn }: { turn: ConversationTurn }) {
  return (
    <>
      {turn.tool_trace.map((item, index) => {
        const result =
          item.result && typeof item.result === "object"
            ? (item.result as Record<string, unknown>)
            : null;
        if (!result || result.error) return null;
        if (item.tool === "search_evidence" && Array.isArray(result.segments)) {
          return (
            <EvidenceSearchResult
              key={index}
              result={result as unknown as EvidenceSearch}
            />
          );
        }
        if (item.tool === "entity_claims" && Array.isArray(result.claims)) {
          return (
            <EntityClaimsResult
              key={index}
              result={result as unknown as EntityClaims}
            />
          );
        }
        return null;
      })}
    </>
  );
}

function ToolActivity({
  turn,
  modelId,
  organizationId,
}: {
  turn: ConversationTurn;
  modelId: string;
  organizationId?: string;
}) {
  const provenance = turn.provenance;
  const hasProvenance = Boolean(
    provenance?.llm?.provider
    || provenance?.llm?.model
    || provenance?.mcp?.server_version
    || provenance?.helios?.api_version
    || turn.request_id,
  );
  if (turn.tool_trace.length === 0 && !hasProvenance) return null;
  return (
    <details className="talk-details">
      <summary>How Helios produced this answer</summary>
      {turn.trace_run_id ? (
        <section className="talk-answer-path" aria-label="Answer path">
          <ol>
            <li>
              <strong>Question understood</strong>
              <span>Helios identified what you wanted to know.</span>
            </li>
            <li>
              <strong>Approved definitions checked</strong>
              <span>
                {turn.tool_trace.some((item) =>
                  ["search_semantics", "describe", "describe_model"].includes(
                    item.tool,
                  )
                )
                  ? "Business terms were matched to the governed model."
                  : "No definition lookup was recorded."}
              </span>
            </li>
            <li>
              <strong>Data source checked</strong>
              <span>
                {turn.tool_trace.some((item) =>
                  ["compile_query", "run_query"].includes(item.tool)
                )
                  ? "An approved data request was prepared."
                  : turn.tool_trace.some((item) =>
                      ["search_evidence", "entity_claims"].includes(item.tool)
                    )
                    ? "Crawled documents were searched; no database query was needed."
                    : "This answer did not need a database query."}
              </span>
            </li>
            <li>
              <strong>Answer returned</strong>
              <span>The response and its evidence were saved together.</span>
            </li>
          </ol>
          <Link
            className="text-link"
            to={`/governance/mcp?${new URLSearchParams({
              ...(organizationId ? { organization: organizationId } : {}),
              model: modelId,
              tab: "traces",
              trace: turn.trace_run_id,
              view: "semantic",
            })}`}
          >
            Open the full answer path
          </Link>
        </section>
      ) : null}
      {hasProvenance ? (
        <dl className="talk-provenance">
          <div>
            <dt>LLM</dt>
            <dd>
              {[provenance?.llm?.provider, provenance?.llm?.model]
                .filter(Boolean).join(" · ") || "Not recorded"}
            </dd>
          </div>
          <div>
            <dt>MCP server</dt>
            <dd>
              {[provenance?.mcp?.server_name, provenance?.mcp?.server_version]
                .filter(Boolean).join(" · ") || "Not recorded"}
            </dd>
          </div>
          <div>
            <dt>MCP protocol</dt>
            <dd>{provenance?.mcp?.protocol_version || "Not recorded"}</dd>
          </div>
          <div>
            <dt>Helios API</dt>
            <dd>{provenance?.helios?.api_version || "Not recorded"}</dd>
          </div>
          <div>
            <dt>Request</dt>
            <dd>{turn.request_id || "Not recorded"}</dd>
          </div>
        </dl>
      ) : null}
      {turn.tool_trace.length ? (
        <ol className="talk-tools">
          {turn.tool_trace.map((item, index) => {
            const result =
              item.result && typeof item.result === "object"
                ? item.result as Record<string, unknown>
                : null;
            return (
              <li key={`${item.tool}-${index}`}>
                <strong>{item.tool}</strong>
                <pre>{JSON.stringify(item.arguments, null, 2)}</pre>
                {typeof result?.message === "string" ? (
                  <p className={result.error ? "inline-message inline-message--error" : ""}>
                    {result.message}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </details>
  );
}

export default function TalkPage({ context }: TalkPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const modelId = context.selectedModelId;
  const requestedConversationId = searchParams.get("conversation") ?? "";
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [active, setActive] = useState<ConversationDetail>();
  const [turns, setTurns] = useState<Record<string, ConversationTurn>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [archivingId, setArchivingId] = useState<string>();
  const [workingMessageIndex, setWorkingMessageIndex] = useState(0);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);
  const model = context.models.find((item) => item.id === modelId);

  const setConversationParam = (conversationId?: string, replace = false) => {
    const next = new URLSearchParams(searchParams);
    if (conversationId) next.set("conversation", conversationId);
    else next.delete("conversation");
    setSearchParams(next, { replace });
  };

  useEffect(() => {
    let cancelled = false;
    if (
      requestedConversationId &&
      requestedConversationId !== "new" &&
      active?.id === requestedConversationId &&
      active.model_id === modelId
    ) {
      return () => { cancelled = true; };
    }
    setConversations([]);
    setActive(undefined);
    setTurns({});
    setError(undefined);
    if (!modelId) return () => { cancelled = true; };
    setLoading(true);
    void context.loadModelConversations(modelId)
      .then(async (collection) => {
        if (cancelled) return;
        setConversations(collection.conversations);
        const targetId = requestedConversationId === "new"
          ? ""
          : requestedConversationId || collection.conversations[0]?.id;
        if (!targetId) return;
        const loaded = await context.loadModelConversation(modelId, targetId);
        if (cancelled) return;
        setActive(loaded);
        setTurns(conversationTurns(loaded));
        if (!requestedConversationId) {
          setConversationParam(targetId, true);
        }
      })
      .catch((loadError) => {
        if (!cancelled) setError(conversationError(loadError));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
    // Context methods are stable callbacks; URL/model changes own this load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId, requestedConversationId]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    if (typeof transcript.scrollTo === "function") {
      transcript.scrollTo({
        top: transcript.scrollHeight,
        behavior: "smooth",
      });
    } else {
      transcript.scrollTop = transcript.scrollHeight;
    }
  }, [active?.messages.length, busy]);

  useEffect(() => {
    if (!busy) {
      setWorkingMessageIndex(0);
      return;
    }
    const interval = window.setInterval(() => {
      setWorkingMessageIndex(
        (current) => (current + 1) % WORKING_MESSAGES.length,
      );
    }, 4_000);
    return () => window.clearInterval(interval);
  }, [busy]);

  const selectConversation = async (conversationId: string) => {
    if (!modelId || conversationId === active?.id) return;
    setError(undefined);
    setLoading(true);
    try {
      const loaded = await context.loadModelConversation(
        modelId,
        conversationId,
      );
      setActive(loaded);
      setTurns(conversationTurns(loaded));
      setConversationParam(conversationId);
    } catch (loadError) {
      setError(conversationError(loadError));
    } finally {
      setLoading(false);
    }
  };

  const startConversation = () => {
    setActive(undefined);
    setTurns({});
    setMessage("");
    setError(undefined);
    setConversationParam("new");
  };

  const archiveConversation = async (
    conversation: ConversationSummary,
  ) => {
    if (!modelId || busy || archivingId) return;
    setArchivingId(conversation.id);
    setError(undefined);
    try {
      await context.archiveModelConversation(
        modelId,
        conversation.id,
      );
      setConversations((items) =>
        items.filter((item) => item.id !== conversation.id)
      );
      if (active?.id === conversation.id) {
        setActive(undefined);
        setTurns({});
        setMessage("");
        setConversationParam("new");
      }
    } catch (archiveError) {
      setError(conversationError(archiveError));
    } finally {
      setArchivingId(undefined);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const question = message.trim();
    if (
      !modelId
      || !question
      || question.length > 10_000
      || busy
      || active?.archived_at
    ) return;
    setBusy(true);
    setError(undefined);
    try {
      const response = active
        ? await context.appendModelConversationTurn(
            modelId,
            active.id,
            question,
            active.version,
          )
        : await context.createModelConversation(modelId, question);
      setActive(response.conversation);
      setConversations((items) => [
        summary(response.conversation),
        ...items.filter((item) => item.id !== response.conversation.id),
      ]);
      const assistant = [...response.conversation.messages]
        .reverse()
        .find((item) => item.role === "assistant");
      if (assistant) {
        setTurns((items) => ({
          ...items,
          [assistant.id]: response.turn,
        }));
      }
      setMessage("");
      setConversationParam(response.conversation.id, !active);
    } catch (submitError) {
      if (submitError instanceof ConflictError && active) {
        try {
          const reloaded = await context.loadModelConversation(
            modelId,
            active.id,
          );
          setActive(reloaded);
          setConversations((items) => [
            summary(reloaded),
            ...items.filter((item) => item.id !== reloaded.id),
          ]);
          setError(
            "This conversation changed in another session and was reloaded. Review it and ask again.",
          );
        } catch (reloadError) {
          setError(conversationError(reloadError));
        }
      } else {
        setError(conversationError(submitError));
      }
    } finally {
      setBusy(false);
    }
  };

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

  if (
    !modelId
    && (context.modelStatus === "loading" || context.modelStatus === "idle")
  ) {
    return <LoadingState label="Loading semantic models…" />;
  }

  if (!modelId && context.modelStatus === "error") {
    return (
      <ErrorState
        title="Semantic models could not be loaded"
        message={context.errorMessage ?? "The API request failed."}
        onRetry={context.retry}
      />
    );
  }

  if (!modelId) {
    const organization = context.organizations.find(
      (item) => item.id === context.selectedOrganizationId,
    );
    const canCreateModel = organization?.available_actions.includes(
      "model.create",
    );
    return (
      <EmptyState
        title={
          context.models.length === 0
            ? "No semantic models available"
            : "Select a model to start a conversation"
        }
        message={
          canCreateModel
            ? "Create a semantic model from the Build workspace, then select it from the Model menu above."
            : "Choose an available model from the Model menu above. If none are listed, ask your Helios administrator for model access."
        }
      />
    );
  }

  return (
    <div className="talk-page">
      <header className="talk-page__header">
        <div>
          <p className="section-eyebrow">Talk to Your Data</p>
          <h1>{model?.name ?? modelId}</h1>
        </div>
        <button
          className="button button--primary"
          type="button"
          onClick={startConversation}
          disabled={busy}
        >
          New conversation
        </button>
      </header>

      <div className="talk-layout">
        <aside className="talk-history" aria-label="Conversation history">
          <h2>Conversations</h2>
          {conversations.length === 0 && !loading ? (
            <p className="talk-history__empty">No saved conversations yet.</p>
          ) : (
            <ul>
              {conversations.map((item) => (
                <li className="talk-history__item" key={item.id}>
                  <button
                    type="button"
                    className={`talk-history__select ${
                      item.id === active?.id ? "is-active" : ""
                    }`}
                    aria-current={item.id === active?.id ? "page" : undefined}
                    onClick={() => void selectConversation(item.id)}
                  >
                    <strong>{item.title}</strong>
                    <span>
                      {new Date(item.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                  <button
                    className="talk-history__archive"
                    type="button"
                    aria-label={`Archive ${item.title}`}
                    title="Archive conversation"
                    disabled={busy || Boolean(archivingId)}
                    onClick={() => void archiveConversation(item)}
                  >
                    {archivingId === item.id ? "Archiving…" : "Archive"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="talk-conversation" aria-label="Conversation">
          {loading && !active ? (
            <LoadingState label="Loading conversation…" />
          ) : (
            <>
              <div
                className="talk-transcript"
                ref={transcriptRef}
                aria-live="polite"
              >
                {!active ? (
                  <div className="talk-welcome">
                    <h2>What would you like to understand?</h2>
                    <p>
                      Ask about available metrics, business concepts, or
                      governed data. Helios will use its MCP tools and your
                      existing permissions.
                    </p>
                  </div>
                ) : (
                  active.messages.map((item) => (
                    <article
                      className={
                        `talk-message talk-message--${item.role}`
                        + (
                          item.role === "assistant"
                          && turns[item.id]?.failure
                            ? " talk-message--failure"
                            : ""
                        )
                      }
                      key={item.id}
                      role={
                        item.role === "assistant" && turns[item.id]?.failure
                          ? "alert"
                          : undefined
                      }
                    >
                      <p className="talk-message__role">
                        {item.role === "user" ? "You" : "Helios"}
                      </p>
                      <div className="talk-message__content">{item.content}</div>
                      {item.role === "assistant" && turns[item.id] ? (
                        <>
                          <DocumentResults turn={turns[item.id]} />
                          <QueryResult turn={turns[item.id]} />
                          <ToolActivity
                            turn={turns[item.id]}
                            modelId={modelId}
                            organizationId={context.selectedOrganizationId}
                          />
                        </>
                      ) : null}
                    </article>
                  ))
                )}
              </div>

              {error ? (
                <div className="inline-message inline-message--error" role="alert">
                  {error}
                </div>
              ) : null}

              {busy ? (
                <div
                  className="talk-progress"
                  role="status"
                  aria-live="polite"
                  aria-label="Helios is working on your answer"
                >
                  <span className="talk-progress__activity" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  <span
                    className="talk-progress__message"
                    key={workingMessageIndex}
                    aria-hidden="true"
                  >
                    {WORKING_MESSAGES[workingMessageIndex]}
                  </span>
                  <span className="sr-only">
                    Helios is working on your answer. This can take several
                    minutes.
                  </span>
                </div>
              ) : null}

              {active?.archived_at ? (
                <div className="inline-message" role="status">
                  This conversation is archived and cannot accept new
                  questions.
                </div>
              ) : null}

              <form className="talk-composer" onSubmit={submit}>
                <label htmlFor="talk-message">Ask about this model</label>
                <textarea
                  id="talk-message"
                  value={message}
                  maxLength={10_000}
                  rows={3}
                  disabled={busy || Boolean(active?.archived_at)}
                  placeholder="Ask a question about your governed data…"
                  onChange={(event) => setMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                />
                <div className="talk-composer__actions">
                  <span>{message.length.toLocaleString()} / 10,000</span>
                  <button
                    className="button button--primary"
                    type="submit"
                    disabled={
                      busy || !message.trim() || Boolean(active?.archived_at)
                    }
                  >
                    {busy ? "Asking…" : "Ask Helios"}
                  </button>
                </div>
              </form>
            </>
          )}
          {loading && active ? (
            <div className="talk-loading-overlay" role="status">
              Loading conversation…
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
