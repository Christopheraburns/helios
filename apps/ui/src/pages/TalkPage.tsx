import {
  FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";

import {
  ApiUnavailableError,
  AuthenticationError,
  AuthorizationError,
  ConflictError,
  ConversationDetail,
  ConversationSummary,
  ConversationTurn,
} from "../api/client";
import { EmptyState, LoadingState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface TalkPageProps {
  context: ApplicationContextState;
}

const RESULT_PAGE_SIZE = 50;

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

function ToolActivity({ turn }: { turn: ConversationTurn }) {
  if (turn.tool_trace.length === 0) return null;
  return (
    <details className="talk-details">
      <summary>How Helios produced this answer</summary>
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
      setConversationParam(conversationId);
    } catch (loadError) {
      setError(conversationError(loadError));
    } finally {
      setLoading(false);
    }
  };

  const startConversation = () => {
    setActive(undefined);
    setMessage("");
    setError(undefined);
    setConversationParam("new");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const question = message.trim();
    if (!modelId || !question || question.length > 10_000 || busy) return;
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

  if (!modelId) {
    return (
      <EmptyState
        title="Select a model to start a conversation"
        message="Talk to Your Data uses the active Helios model and its permissions."
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
                <li key={item.id}>
                  <button
                    type="button"
                    className={item.id === active?.id ? "is-active" : ""}
                    aria-current={item.id === active?.id ? "page" : undefined}
                    onClick={() => void selectConversation(item.id)}
                  >
                    <strong>{item.title}</strong>
                    <span>
                      {new Date(item.updated_at).toLocaleDateString()}
                    </span>
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
                    <p className="section-eyebrow">Model-aware assistant</p>
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
                      className={`talk-message talk-message--${item.role}`}
                      key={item.id}
                    >
                      <p className="talk-message__role">
                        {item.role === "user" ? "You" : "Helios"}
                      </p>
                      <div className="talk-message__content">{item.content}</div>
                      {item.role === "assistant" && turns[item.id] ? (
                        <>
                          <QueryResult turn={turns[item.id]} />
                          <ToolActivity turn={turns[item.id]} />
                        </>
                      ) : null}
                    </article>
                  ))
                )}
                {busy ? (
                  <div className="talk-thinking" role="status">
                    <span aria-hidden="true" />
                    Helios is reasoning through the model and MCP tools…
                  </div>
                ) : null}
              </div>

              {error ? (
                <div className="inline-message inline-message--error" role="alert">
                  {error}
                </div>
              ) : null}

              <form className="talk-composer" onSubmit={submit}>
                <label htmlFor="talk-message">Ask about this model</label>
                <textarea
                  id="talk-message"
                  value={message}
                  maxLength={10_000}
                  rows={3}
                  disabled={busy}
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
                    disabled={busy || !message.trim()}
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
