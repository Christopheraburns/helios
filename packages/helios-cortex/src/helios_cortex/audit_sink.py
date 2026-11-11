"""Structured audit records the libraries emit, and the sink the host routes them to.

A library reports what it did for a caller (a tool call, a publish) as an
AuditRecord and hands it to the host's AuditSink. The default sink,
LoggingAuditSink, writes one log line per record to the ``helios_cortex.audit_sink``
logger, with the whole record in ``extra["helios_audit"]`` for structured handlers.
A host with its own audit store implements ``record``. A record never holds a
token, SQL text or result rows: the caller is a username and SQL is a fingerprint.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Literal, Protocol

LOGGER = logging.getLogger(__name__)

Outcome = Literal["success", "denied", "error"]


def sql_fingerprint(sql: str) -> str:
    """SHA-256 of the SQL with whitespace collapsed: identifies a statement without storing it."""
    normalized = " ".join(sql.split()).encode()
    return hashlib.sha256(normalized).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """One thing a library did for a caller.

    ``action`` is the tool or operation (``run_query``, ``publish``). ``error_code``
    is set when the action returned an error to the caller, ``exception`` (the type
    name) when it raised. ``details`` is free-form diagnostic context; sinks that
    persist it should redact it."""

    action: str
    outcome: Outcome
    model: str | None = None
    caller: str | None = None
    row_count: int | None = None
    sql_fingerprint: str | None = None
    error_code: str | None = None
    exception: str | None = None
    duration_ms: float | None = None
    details: Mapping[str, object] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        if self.outcome not in ("success", "denied", "error"):
            raise ValueError(f"unknown audit outcome {self.outcome!r}")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def as_dict(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self)} | {"details": dict(self.details)}


class AuditSink(Protocol):
    def record(self, event: AuditRecord) -> None: ...


class LoggingAuditSink:
    """The default sink: INFO for successes, WARNING for denials and errors."""

    def record(self, event: AuditRecord) -> None:
        LOGGER.log(
            logging.INFO if event.outcome == "success" else logging.WARNING,
            "%s %s model=%s caller=%s rows=%s sql=%s error=%s",
            event.action,
            event.outcome,
            event.model,
            event.caller,
            event.row_count,
            event.sql_fingerprint[:12] if event.sql_fingerprint else None,
            event.error_code or event.exception,
            extra={"helios_audit": event.as_dict()},
        )


def record_safely(sink: AuditSink, event: AuditRecord) -> None:
    """Hand ``event`` to ``sink``; a failing sink is logged and never fails the action."""
    try:
        sink.record(event)
    except Exception:
        LOGGER.exception("audit sink %s failed to record %s", type(sink).__name__, event.action)
