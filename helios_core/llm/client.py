"""
LLM client with two interchangeable backends:

  LLM_PROVIDER=anthropic   Anthropic Messages API (ANTHROPIC_API_KEY, ANTHROPIC_MODEL)
  LLM_PROVIDER=openai      any OpenAI-compatible endpoint, e.g. Cloudera AI Inference
                           (INFERENCE_BASE_URL, INFERENCE_API_KEY, INFERENCE_MODEL)

Both expose complete_json(): ask for a JSON object, parse it, retry once on malformed output.
Uses httpx directly so no provider SDK is needed in the runtime.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, provider: str, model: str, api_key: str | None, base_url: str | None = None,
                 max_tokens: int = 4000, temperature: float = 0.0, timeout: float = 120.0):
        self.provider, self.model, self.api_key, self.base_url = provider, model, api_key, base_url
        self.max_tokens, self.temperature, self.timeout = max_tokens, temperature, timeout
        self.calls = 0
        self.input_chars = 0

    # ------------------------------------------------------------ raw completion
    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.input_chars += len(system) + len(user)
        for attempt in range(3):
            try:
                return self._anthropic(system, user) if self.provider == "anthropic" else self._openai(system, user)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 500, 502, 503, 529) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise LLMError(f"{self.provider} {e.response.status_code}: {e.response.text[:300]}") from e
            except httpx.HTTPError as e:
                raise LLMError(f"{self.provider} request failed: {e}") from e
        raise LLMError("unreachable")

    def _anthropic(self, system: str, user: str) -> str:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=self.timeout,
                       headers={"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       json={"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                             "system": system, "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")

    def _openai(self, system: str, user: str) -> str:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = httpx.post(f"{(self.base_url or '').rstrip('/')}/chat/completions", timeout=self.timeout, headers=headers,
                       json={"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------ JSON completion
    def complete_json(self, system: str, user: str) -> Any:
        system_json = system + "\n\nRespond with a single JSON object and nothing else: no prose, no markdown fences."
        text = self.complete(system_json, user)
        try:
            return _parse_json(text)
        except ValueError:
            text = self.complete(system_json, user + "\n\nYour previous reply was not valid JSON. Return only the JSON object.")
            return _parse_json(text)

    def ping(self) -> bool:
        return isinstance(self.complete_json("You are a JSON echo service.", 'Return {"ok": true}'), dict)


def _parse_json(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError("no JSON object in response")


def llm_from_env() -> LLMClient | None:
    provider = (os.environ.get("LLM_PROVIDER") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai")).lower()
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        return LLMClient("anthropic", os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"), key)
    base = os.environ.get("INFERENCE_BASE_URL")
    if not base:
        return None
    return LLMClient("openai", os.environ.get("INFERENCE_MODEL", ""), os.environ.get("INFERENCE_API_KEY"), base)
