"""Session-scoped, in-memory LLM provider overrides for Talk to Your Data."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from helios_core.llm import LLMClient, llm_from_env
from helios_core.llm.client import inference_token

SUPPORTED_PROVIDERS = frozenset({
    "anthropic",
    "mistral",
    "bedrock",
    "openai",
})
LITELLM_GATEWAY_URL = "https://ai-gateway.cloudops.cloudera.com"


@dataclass(frozen=True)
class SessionModelProvider:
    provider: str
    model: str
    api_key: str


class SessionModelProviderStore:
    """Keep credentials in process memory, scoped to principal and browser session."""

    def __init__(self, expiry_seconds: float = 12 * 60 * 60) -> None:
        self._settings: dict[
            tuple[str, str],
            tuple[float, SessionModelProvider],
        ] = {}
        self._lock = threading.RLock()
        self._expiry_seconds = expiry_seconds

    def get(
        self,
        principal_id: str,
        session_id: str,
    ) -> SessionModelProvider | None:
        with self._lock:
            entry = self._settings.get((principal_id, session_id))
            if entry is None:
                return None
            expires_at, settings = entry
            if expires_at <= time.monotonic():
                self._settings.pop((principal_id, session_id), None)
                return None
            return settings

    def set(
        self,
        principal_id: str,
        session_id: str,
        settings: SessionModelProvider,
    ) -> None:
        with self._lock:
            now = time.monotonic()
            self._settings = {
                key: entry
                for key, entry in self._settings.items()
                if entry[0] > now
            }
            self._settings[(principal_id, session_id)] = (
                now + self._expiry_seconds,
                settings,
            )

    def delete(self, principal_id: str, session_id: str) -> bool:
        with self._lock:
            return self._settings.pop((principal_id, session_id), None) is not None


DEFAULT_SESSION_MODEL_PROVIDER_STORE = SessionModelProviderStore()


def llm_for_settings(settings: SessionModelProvider) -> LLMClient:
    provider = settings.provider
    if provider == "anthropic":
        return LLMClient(provider, settings.model, settings.api_key)
    if provider == "mistral":
        return LLMClient(
            provider,
            settings.model,
            settings.api_key,
            os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
        )
    if provider == "bedrock":
        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        return LLMClient(
            provider,
            settings.model,
            settings.api_key,
            f"https://bedrock-runtime.{region}.amazonaws.com",
        )
    if provider == "openai":
        return LLMClient(
            provider,
            settings.model,
            settings.api_key,
            openai_compatible_base_url(),
        )
    raise ValueError("unsupported model provider")


def environment_provider_summary() -> tuple[str | None, str | None]:
    configured = llm_from_env()
    if configured is None:
        return None, None
    return configured.provider, configured.model


def openai_compatible_base_url() -> str:
    """Use the deployment override, otherwise the Cloudera LiteLLM gateway."""
    configured = os.environ.get("INFERENCE_BASE_URL", "").strip()
    return configured or LITELLM_GATEWAY_URL


def deployment_defaults(provider: str) -> tuple[str | None, str | None]:
    """The model and key the deployment supplies for a provider chosen on the
    LLM Provider page, so the user need not enter their own. Only the
    OpenAI-compatible endpoint has them (INFERENCE_MODEL, INFERENCE_TOKEN)."""
    if provider != "openai":
        return None, None
    return os.environ.get("INFERENCE_MODEL", "").strip() or None, inference_token()


def provider_availability() -> dict[str, bool]:
    return {
        "anthropic": True,
        "mistral": True,
        "bedrock": True,
        "openai": True,
    }
