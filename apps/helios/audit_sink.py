"""The old console's AuditSink: library audit records into its SQLite audit log, until Phase 8."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.helios.console import audit
from helios_cortex.audit_sink import AuditRecord
from apps.helios.console.metadata import MetadataRepository


class RepositoryAuditSink:
    """Persist records as ``<component>.tool`` audit events. ``repository`` is a callable
    so the sink follows the process's current repository (tests replace it)."""

    def __init__(self, repository: Callable[[], MetadataRepository], component: str):
        self._repository = repository
        self.component = component

    def record(self, event: AuditRecord) -> None:
        details: dict[str, Any] = dict(event.details)
        if event.exception:
            details["error_type"] = event.exception
        if event.error_code:
            details["error_code"] = event.error_code
        if event.row_count is not None:
            details["row_count"] = event.row_count
        if event.sql_fingerprint:
            details["compiled_fingerprint"] = event.sql_fingerprint
        failed = event.exception is not None
        audit.emit(
            self._repository(),
            component=self.component,
            event_type=f"{self.component}.tool",
            action=event.action,
            outcome=event.outcome,
            severity="error" if failed else "info" if event.outcome == "success" else "warning",
            resource_type="model",
            resource_id=event.model,
            model_id=event.model,
            duration_ms=event.duration_ms,
            summary=f"{self.component.upper()} tool {event.action} {'failed' if failed else 'completed'}",
            details=details,
        )
