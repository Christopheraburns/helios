"""Structured, bounded tracing for LLM and MCP execution."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from helios_core.metadata import MetadataRepository, TraceRun, TraceSpan

PROMPT_VERSION = "talk-to-data-v1"
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "delegation",
    "password",
    "secret",
    "token",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitized(value: Any, *, depth: int = 0) -> Any:
    """Remove credentials while retaining diagnostic request structure."""
    if depth > 12:
        return "[maximum depth]"
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return sanitized(model_dump(mode="json"), depth=depth + 1)
        except (TypeError, ValueError):
            return sanitized(model_dump(), depth=depth + 1)
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if any(part in str(key).casefold() for part in _SENSITIVE_KEYS)
                else sanitized(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitized(item, depth=depth + 1) for item in value[:1000]]
    if isinstance(value, str) and len(value) > 100_000:
        return value[:100_000] + "…"
    return value


class TraceRecorder:
    def __init__(
        self,
        repository: MetadataRepository,
        run: TraceRun,
    ) -> None:
        self.repository = repository
        self.run = repository.create_trace_run(run)
        self._sequence = 0
        self._lock = threading.Lock()
        self.finished = False

    @classmethod
    def start(
        cls,
        repository: MetadataRepository,
        *,
        principal_id: str,
        organization_id: str,
        model_id: str,
        question: str,
        provider: str,
        llm_model: str,
        request_id: str | None,
        purpose: str = "conversation",
        question_id: str | None = None,
        run_id: str | None = None,
    ) -> "TraceRecorder":
        return cls(
            repository,
            TraceRun(
                id=run_id or str(uuid.uuid4()),
                request_id=request_id,
                conversation_id=None,
                principal_id=principal_id,
                organization_id=organization_id,
                model_id=model_id,
                purpose=purpose,
                question_id=question_id,
                question=question,
                llm_provider=provider,
                llm_model=llm_model,
                prompt_version=PROMPT_VERSION,
                status="running",
                started_at=utcnow(),
            ),
        )

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def span(
        self,
        *,
        component: str,
        kind: str,
        name: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        input: Any = None,
        output: Any = None,
        attributes: dict[str, Any] | None = None,
        error: str | None = None,
        parent_span_id: str | None = None,
        sequence: int | None = None,
        span_id: str | None = None,
    ) -> TraceSpan:
        span = TraceSpan(
            id=span_id or str(uuid.uuid4()),
            run_id=self.run.id,
            parent_span_id=parent_span_id,
            sequence=sequence or self.next_sequence(),
            component=component,
            kind=kind,
            name=name,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=max(
                (completed_at - started_at).total_seconds() * 1000,
                0,
            ),
            input=sanitized(input),
            output=sanitized(output),
            attributes=sanitized(attributes or {}),
            error=(error[:10_000] if error else None),
        )
        return self.repository.append_trace_span(span)

    def finish(
        self,
        *,
        status: str,
        termination_reason: str,
        answer: str | None,
        tokens_in: int,
        tokens_out: int,
        conversation_id: str | None = None,
    ) -> TraceRun:
        if self.finished:
            current = self.repository.trace_run(self.run.id)
            return current or self.run
        completed_at = utcnow()
        result = self.repository.update_trace_run(
            self.run.id,
            status=status,
            termination_reason=termination_reason,
            answer=answer,
            completed_at=completed_at,
            duration_ms=max(
                (completed_at - self.run.started_at).total_seconds() * 1000,
                0,
            ),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            conversation_id=conversation_id,
        )
        self.finished = True
        return result
