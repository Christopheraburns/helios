"""Session-scoped MCP conversation settings."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


MIN_TOOL_ROUNDS = 1
MAX_TOOL_ROUNDS = 20
DEFAULT_TOOL_ROUNDS = 6


def environment_max_tool_rounds() -> int:
    raw_value = os.environ.get(
        "HELIOS_MCP_MAX_TOOL_ROUNDS",
        str(DEFAULT_TOOL_ROUNDS),
    )
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            "HELIOS_MCP_MAX_TOOL_ROUNDS must be an integer from "
            f"{MIN_TOOL_ROUNDS} to {MAX_TOOL_ROUNDS}"
        ) from exc
    if not MIN_TOOL_ROUNDS <= value <= MAX_TOOL_ROUNDS:
        raise ValueError(
            "HELIOS_MCP_MAX_TOOL_ROUNDS must be an integer from "
            f"{MIN_TOOL_ROUNDS} to {MAX_TOOL_ROUNDS}"
        )
    return value


@dataclass(frozen=True)
class SessionMCPSettings:
    max_tool_rounds: int


class SessionMCPSettingsStore:
    """Keep per-user MCP overrides in API-process memory."""

    def __init__(self, expiry_seconds: float = 12 * 60 * 60) -> None:
        self._settings: dict[
            tuple[str, str],
            tuple[float, SessionMCPSettings],
        ] = {}
        self._lock = threading.RLock()
        self._expiry_seconds = expiry_seconds

    def get(
        self,
        principal_id: str,
        session_id: str,
    ) -> SessionMCPSettings | None:
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
        settings: SessionMCPSettings,
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


DEFAULT_SESSION_MCP_SETTINGS_STORE = SessionMCPSettingsStore()
