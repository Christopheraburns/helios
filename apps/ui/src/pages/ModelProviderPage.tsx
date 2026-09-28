import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  ModelProviderId,
  ModelProviderSettings,
} from "../api/client";
import {
  ErrorState,
  LoadingState,
} from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

interface ModelProviderPageProps {
  context: ApplicationContextState;
}

const PROVIDERS: Array<{
  id: ModelProviderId;
  label: string;
  description: string;
  models: string[];
}> = [
  {
    id: "anthropic",
    label: "Anthropic",
    description: "Use an Anthropic API key with a Claude model.",
    models: [
      "claude-haiku-4-5-20251001",
      "claude-sonnet-4-5-20250929",
      "claude-sonnet-5",
    ],
  },
  {
    id: "mistral",
    label: "Mistral",
    description: "Use the Mistral API configured for this Helios deployment.",
    models: [
      "mistral-small-latest",
      "mistral-medium-latest",
      "mistral-large-latest",
    ],
  },
  {
    id: "bedrock",
    label: "Amazon Bedrock",
    description: "Use a Bedrock API key in the deployment's AWS region.",
    models: [
      "amazon.nova-pro-v1:0",
      "amazon.nova-lite-v1:0",
      "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ],
  },
  {
    id: "openai",
    label: "OpenAI-compatible",
    description: "Use the administrator-configured inference endpoint.",
    models: [],
  },
];

export default function ModelProviderPage({
  context,
}: ModelProviderPageProps) {
  const [settings, setSettings] = useState<ModelProviderSettings>();
  const [provider, setProvider] = useState<ModelProviderId>("anthropic");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "success" | "error"
  >("idle");
  const [message, setMessage] = useState("");

  const selectedProvider = useMemo(
    () => PROVIDERS.find((item) => item.id === provider) ?? PROVIDERS[0],
    [provider],
  );
  const availability = new Map(
    settings?.providers.map((item) => [item.id, item.available]) ?? [],
  );

  useEffect(() => {
    let active = true;
    void context.loadModelProviderSettings()
      .then((loaded) => {
        if (!active) return;
        setSettings(loaded);
        if (loaded.provider) setProvider(loaded.provider);
        setModel(loaded.model ?? "");
        setLoadState("ready");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setMessage(
          error instanceof Error
            ? error.message
            : "Model provider settings could not be loaded.",
        );
        setLoadState("error");
      });
    return () => {
      active = false;
    };
  }, [context.loadModelProviderSettings]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaveState("saving");
    setMessage("");
    try {
      const updated = await context.updateModelProviderSettings({
        provider,
        model: model.trim(),
        api_key: apiKey,
      });
      setSettings(updated);
      setApiKey("");
      setSaveState("success");
      setMessage(
        "Session override saved. New Talk conversations will use this model.",
      );
    } catch (error) {
      setSaveState("error");
      setMessage(
        error instanceof Error
          ? error.message
          : "Model provider settings could not be saved.",
      );
    }
  }

  async function useDefault() {
    setSaveState("saving");
    setMessage("");
    try {
      const updated = await context.deleteModelProviderSettings();
      setSettings(updated);
      setProvider(updated.provider ?? "anthropic");
      setModel(updated.model ?? "");
      setApiKey("");
      setSaveState("success");
      setMessage("The project environment default is active.");
    } catch (error) {
      setSaveState("error");
      setMessage(
        error instanceof Error
          ? error.message
          : "The session override could not be cleared.",
      );
    }
  }

  if (loadState === "loading") {
    return <LoadingState label="Loading model provider settings…" />;
  }
  if (loadState === "error") {
    return (
      <ErrorState
        title="Model provider settings unavailable"
        message={message}
      />
    );
  }

  return (
    <div className="provider-settings">
      <header className="page-header">
        <div>
          <p className="page-header__eyebrow">Govern</p>
          <h1>AI Model Provider</h1>
          <p>
            Override the project model provider for this browser session and
            your signed-in identity.
          </p>
        </div>
        <span className={`provider-settings__source provider-settings__source--${
          settings?.source ?? "environment"
        }`}>
          {settings?.source === "session"
            ? "Session override"
            : "Project default"}
        </span>
      </header>

      <section className="provider-settings__security" aria-label="Credential storage">
        <strong>Session-only credential</strong>
        <p>
          Helios keeps this API key only in API-process memory. It is not
          returned to the browser, written to metadata, or included in audit
          details. It expires after 12 hours and restarting the API clears it.
        </p>
      </section>

      <form className="provider-settings__form" onSubmit={save}>
        <label>
          <span>Provider</span>
          <select
            aria-label="Provider"
            value={provider}
            onChange={(event) => {
              const next = event.currentTarget.value as ModelProviderId;
              setProvider(next);
              setModel(
                PROVIDERS.find((item) => item.id === next)?.models[0] ?? "",
              );
              setSaveState("idle");
            }}
          >
            {PROVIDERS.map((item) => (
              <option
                key={item.id}
                value={item.id}
                disabled={availability.get(item.id) === false}
              >
                {item.label}
                {availability.get(item.id) === false
                  ? " — administrator setup required"
                  : ""}
              </option>
            ))}
          </select>
          <small>{selectedProvider.description}</small>
        </label>

        <label>
          <span>Model</span>
          <input
            aria-label="Model"
            list={`provider-models-${provider}`}
            value={model}
            maxLength={300}
            required
            placeholder="Enter the provider model ID"
            onChange={(event) => setModel(event.currentTarget.value)}
          />
          <datalist id={`provider-models-${provider}`}>
            {selectedProvider.models.map((item) => (
              <option value={item} key={item} />
            ))}
          </datalist>
          <small>
            Select a suggested model or enter an exact model ID supported by
            your account.
          </small>
        </label>

        <label>
          <span>API key</span>
          <input
            aria-label="API key"
            type="password"
            value={apiKey}
            required
            maxLength={10_000}
            autoComplete="off"
            placeholder={
              settings?.api_key_configured
                ? "Enter a key to replace the current session key"
                : "Enter an API key"
            }
            onChange={(event) => setApiKey(event.currentTarget.value)}
          />
          <small>The existing key is never displayed or returned.</small>
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
              || !model.trim()
              || !apiKey.trim()
              || availability.get(provider) === false
            }
          >
            {saveState === "saving" ? "Saving…" : "Use for this session"}
          </button>
          <button
            className="button button--secondary"
            type="button"
            disabled={saveState === "saving" || settings?.source !== "session"}
            onClick={() => void useDefault()}
          >
            Use project default
          </button>
        </div>
      </form>
    </div>
  );
}
