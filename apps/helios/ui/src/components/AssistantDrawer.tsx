import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  AssistantAction,
  AssistantJourney,
  AssistantTurnResponse,
  ServiceUnavailableError,
} from "../api/client";
import { ApplicationContextState } from "../hooks/useApplicationContext";
import "./AssistantDrawer.css";

interface AssistantDrawerProps {
  context: ApplicationContextState;
  open: boolean;
  onClose: () => void;
}

interface TranscriptMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  actions?: AssistantAction[];
  provenance?: AssistantTurnResponse["provenance"];
}

const suggestions = [
  "What should I do next?",
  "Walk me through creating a semantic model",
  "Explain this page",
];

const providerHint =
  "The assistant needs an LLM provider. Configure one under Settings › LLM Provider.";

export function mergeContextParams(
  context: Pick<ApplicationContextState, "selectedOrganizationId" | "selectedModelId">,
  params: Record<string, string>,
): string {
  const search = new URLSearchParams();
  if (context.selectedOrganizationId) {
    search.set("organization", context.selectedOrganizationId);
  }
  if (context.selectedOrganizationId && context.selectedModelId) {
    search.set("model", context.selectedModelId);
  }
  for (const [key, value] of Object.entries(params)) {
    search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export default function AssistantDrawer({
  context,
  open,
  onClose,
}: AssistantDrawerProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{ kind: "provider" | "generic"; text: string }>();
  const [journeys, setJourneys] = useState<AssistantJourney[]>([]);
  const [progressOpen, setProgressOpen] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const organizationId = context.selectedOrganizationId;
  const modelId = context.selectedModelId;
  const ready = context.status === "ready" && Boolean(organizationId);

  const refreshProgress = useCallback(() => {
    if (!ready) return;
    let cancelled = false;
    Promise.resolve()
      .then(() =>
        context.loadAssistantWorkspaceState(organizationId, modelId || undefined),
      )
      .then((state) => {
        if (!cancelled) setJourneys(state.journeys);
      })
      .catch(() => {
        if (!cancelled) setJourneys([]);
      });
    return () => {
      cancelled = true;
    };
  }, [context.loadAssistantWorkspaceState, modelId, organizationId, ready]);

  useEffect(() => {
    if (!open) return;
    return refreshProgress();
  }, [open, refreshProgress]);

  useEffect(() => {
    if (!open) return;
    textareaRef.current?.focus();
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, pending]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || pending || !organizationId) return;
    setError(undefined);
    setDraft("");
    const history = messages
      .slice(-12)
      .map(({ role, content }) => ({ role, content }));
    setMessages((current) => [
      ...current,
      { id: nextId.current++, role: "user", content: message },
    ]);
    setPending(true);
    try {
      const response = await context.sendAssistantTurn({
        organization: organizationId,
        model: modelId || null,
        message,
        history,
        location: { pathname: location.pathname, search: location.search },
      });
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: "assistant",
          content: response.answer,
          actions: response.actions,
          provenance: response.provenance,
        },
      ]);
      if (response.actions.length > 0) refreshProgress();
    } catch (cause) {
      if (cause instanceof ServiceUnavailableError) {
        setError({ kind: "provider", text: providerHint });
      } else {
        setError({
          kind: "generic",
          text:
            cause instanceof Error ? cause.message : "The assistant failed.",
        });
      }
    } finally {
      setPending(false);
      textareaRef.current?.focus();
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void send(draft);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(draft);
    }
  };

  const runAction = (action: AssistantAction) => {
    navigate({
      pathname: action.route,
      search: mergeContextParams(context, action.params),
    });
  };

  return (
    <aside
      className={`assistant-drawer${open ? " assistant-drawer--open" : ""}`}
      aria-label="Navigation assistant"
      aria-hidden={!open}
      hidden={!open}
    >
      <header className="assistant-drawer__header">
        <h2>Assistant</h2>
        <button
          className="assistant-drawer__close"
          type="button"
          aria-label="Close assistant"
          onClick={onClose}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>
      </header>

      <section className="assistant-drawer__progress" aria-label="Your progress">
        <button
          className="assistant-drawer__progress-toggle"
          type="button"
          aria-expanded={progressOpen}
          onClick={() => setProgressOpen((value) => !value)}
        >
          <span>Your progress</span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d={progressOpen ? "m6 14 6-6 6 6" : "m6 10 6 6 6-6"} />
          </svg>
        </button>
        {progressOpen ? (
          journeys.length > 0 ? (
            <ul className="assistant-drawer__journeys">
              {journeys.map((journey) => {
                const next = journey.steps.find(
                  (step) => step.id === journey.next_step_id,
                );
                return (
                  <li key={journey.id}>
                    <div className="assistant-drawer__journey-title">
                      <strong>{journey.title}</strong>
                      <span className="count-badge">
                        {journey.progress.done} of {journey.progress.total}
                      </span>
                    </div>
                    <p>
                      {next ? `Next: ${next.title}` : "All steps complete"}
                    </p>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="assistant-drawer__muted">
              Progress appears once the workspace has loaded.
            </p>
          )
        ) : null}
      </section>

      <div
        className="assistant-drawer__transcript"
        ref={transcriptRef}
        aria-live="polite"
      >
        {messages.length === 0 ? (
          <div className="assistant-drawer__suggestions">
            <p className="assistant-drawer__muted">
              Ask where to go next or how a page works.
            </p>
            {suggestions.map((suggestion) => (
              <button
                className="assistant-drawer__chip"
                type="button"
                key={suggestion}
                disabled={pending || !ready}
                onClick={() => void send(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        ) : null}
        {messages.map((message) => (
          <article
            className={`assistant-drawer__message assistant-drawer__message--${message.role}`}
            key={message.id}
          >
            <p className="assistant-drawer__message-body">{message.content}</p>
            {message.actions && message.actions.length > 0 ? (
              <div className="assistant-drawer__actions">
                {message.actions.map((action, index) => (
                  <button
                    className="button button--secondary assistant-drawer__action"
                    type="button"
                    key={`${action.route}-${index}`}
                    onClick={() => runAction(action)}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            ) : null}
            {message.provenance ? (
              <p className="assistant-drawer__provenance">
                via {message.provenance.llm.provider}/{message.provenance.llm.model}
              </p>
            ) : null}
          </article>
        ))}
        {pending ? (
          <p className="assistant-drawer__status" role="status">
            Thinking…
          </p>
        ) : null}
        {error ? (
          <p className="assistant-drawer__error" role="alert">
            {error.text}
            {error.kind === "provider" ? (
              <>
                {" "}
                <Link
                  to={{
                    pathname: "/governance/model-provider",
                    search: mergeContextParams(context, {}),
                  }}
                >
                  Open LLM Provider settings
                </Link>
              </>
            ) : null}
          </p>
        ) : null}
      </div>

      <form className="assistant-drawer__composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="assistant-drawer-input">
          Ask the assistant
        </label>
        <textarea
          id="assistant-drawer-input"
          ref={textareaRef}
          rows={2}
          value={draft}
          placeholder="Ask the assistant"
          disabled={pending || !ready}
          onChange={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={onKeyDown}
        />
        <button
          className="button button--primary"
          type="submit"
          disabled={pending || !ready || !draft.trim()}
        >
          Send
        </button>
      </form>
    </aside>
  );
}
