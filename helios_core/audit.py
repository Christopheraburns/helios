"""Structured, redacted audit events shared by Helios applications and jobs."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import traceback
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from helios_core.metadata import AuditEvent, MetadataRepository

LOGGER = logging.getLogger("helios.audit")
MAX_DETAIL_BYTES = 8_192
MAX_STRING_LENGTH = 500
MAX_COLLECTION_ITEMS = 50
SENSITIVE_KEY_PARTS = {
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "prompt",
    "question",
    "answer",
    "message",
    "content",
    "sql",
    "query",
    "statement",
    "query_rows",
    "rows",
    "request_body",
    "response_body",
}
CLIENT_EVENT_ACTIONS = frozenset(
    {
        "navigation.view",
        "context.organization_select",
        "context.model_select",
        "workspace.collapse",
        "workspace.expand",
        "canvas.lens_change",
        "canvas.node_focus",
        "canvas.node_select",
        "activity.filter_change",
    }
)
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class AuditContext:
    request_id: str
    session_id: str | None = None
    principal_id: str | None = None
    organization_id: str | None = None
    model_id: str | None = None


_context: ContextVar[AuditContext | None] = ContextVar(
    "helios_audit_context", default=None
)


def current_context() -> AuditContext | None:
    return _context.get()


def set_context(context: AuditContext) -> Token:
    return _context.set(context)


def reset_context(token: Token) -> None:
    _context.reset(token)


def update_context(**changes: str | None) -> None:
    context = current_context()
    if context is not None:
        _context.set(replace(context, **changes))


def new_request_id() -> str:
    return str(uuid.uuid4())


def normalize_correlation_id(value: str | None) -> str | None:
    candidate = (value or "").strip()
    return candidate if _CORRELATION_PATTERN.fullmatch(candidate) else None


def sql_fingerprint(sql: str) -> str:
    normalized = " ".join(sql.split()).encode()
    return hashlib.sha256(normalized).hexdigest()


def exception_diagnostics(
    exc: BaseException,
    *,
    stage: str,
    excluded_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build administrator-only diagnostics without persisting known secrets."""
    secrets = {
        value
        for name, value in os.environ.items()
        if value
        and len(value) >= 4
        and any(
            part in name.lower()
            for part in (
                "credential",
                "password",
                "secret",
                "token",
                "api_key",
                "apikey",
            )
        )
    }
    secrets.update(value for value in excluded_values if value)

    def scrub(value: str) -> str:
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[redacted]")
        return value

    chain: list[dict[str, str]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append(
            {
                "exception_type": type(current).__name__,
                "description": scrub(str(current) or repr(current)),
            }
        )
        current = current.__cause__ or current.__context__

    frames = [
        {
            "file": scrub(frame.filename),
            "line": frame.lineno,
            "function": frame.name,
        }
        for frame in traceback.extract_tb(exc.__traceback__)[-20:]
    ]
    return {
        "stage": stage,
        "exception_chain": chain,
        "stack": frames,
    }


def redact_details(value: Any) -> dict[str, Any]:
    redacted = _redact(value, depth=0)
    if not isinstance(redacted, dict):
        redacted = {"value": redacted}
    encoded = json.dumps(redacted, default=str).encode()
    if len(encoded) <= MAX_DETAIL_BYTES:
        return redacted
    return {
        "truncated": True,
        "keys": sorted(redacted)[:MAX_COLLECTION_ITEMS],
    }


def emit(
    repository: MetadataRepository,
    *,
    component: str,
    event_type: str,
    action: str,
    outcome: str,
    summary: str,
    severity: str = "info",
    resource_type: str | None = None,
    resource_id: str | None = None,
    http_status: int | None = None,
    duration_ms: float | None = None,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    principal_id: str | None = None,
    organization_id: str | None = None,
    model_id: str | None = None,
) -> AuditEvent | None:
    context = current_context()
    event = AuditEvent(
        id=str(uuid.uuid4()),
        occurred_at=datetime.now(timezone.utc),
        request_id=request_id or (context.request_id if context else None),
        session_id=session_id or (context.session_id if context else None),
        principal_id=principal_id or (context.principal_id if context else None),
        organization_id=organization_id
        or (context.organization_id if context else None),
        model_id=model_id or (context.model_id if context else None),
        component=_limited(component, 80),
        event_type=_limited(event_type, 120),
        action=_limited(action, 120),
        resource_type=_optional_limited(resource_type, 80),
        resource_id=_optional_limited(resource_id, 300),
        outcome=_limited(outcome, 40),
        severity=_limited(severity, 20),
        http_status=http_status,
        duration_ms=round(duration_ms, 3) if duration_ms is not None else None,
        summary=_limited(summary, 500),
        details=redact_details(details or {}),
    )
    try:
        return repository.append_audit_event(event)
    except Exception:
        LOGGER.exception(
            "failed to persist audit event component=%s action=%s",
            event.component,
            event.action,
        )
        return None


def retention_days() -> int:
    value = os.environ.get("HELIOS_AUDIT_RETENTION_DAYS", "30")
    try:
        return min(max(int(value), 1), 3650)
    except ValueError:
        LOGGER.warning(
            "invalid HELIOS_AUDIT_RETENTION_DAYS; using 30 days"
        )
        return 30


def purge_expired(repository: MetadataRepository) -> int:
    before = datetime.now(timezone.utc) - timedelta(days=retention_days())
    try:
        return repository.purge_audit_events(before)
    except Exception:
        LOGGER.exception("failed to purge expired audit events")
        return 0


def _redact(value: Any, *, depth: int) -> Any:
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
            name = str(key)
            normalized = name.lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                output[name] = "[redacted]"
            else:
                output[name] = _redact(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple, set)):
        return [
            _redact(item, depth=depth + 1)
            for item in list(value)[:MAX_COLLECTION_ITEMS]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _limited(str(value), MAX_STRING_LENGTH)


def _limited(value: str, maximum: int) -> str:
    value = value.strip()
    return value[:maximum]


def _optional_limited(value: str | None, maximum: int) -> str | None:
    return _limited(value, maximum) if value else None
