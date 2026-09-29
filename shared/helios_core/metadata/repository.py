"""Repository contracts and records for Helios operational metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from helios_core import authz
from helios_core.domain import DataSource, Model, Organization


@dataclass(frozen=True)
class PrincipalRecord:
    id: str
    external_identity: str
    display_name: str
    kind: authz.PrincipalKind = authz.PrincipalKind.HUMAN


@dataclass(frozen=True)
class StoredOrganization:
    organization: Organization
    slug: str


@dataclass(frozen=True)
class StoredModel:
    model: Model
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    conversation_id: str
    position: int
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class StoredConversationTurn:
    id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    created_at: datetime
    request_id: str | None = None
    tool_trace: tuple[dict[str, Any], ...] = ()
    query_result: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    trace_run_id: str | None = None


@dataclass(frozen=True)
class StoredConversation:
    id: str
    model_id: str
    principal_id: str
    title: str
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    messages: tuple[ConversationMessage, ...] = ()
    turns: tuple[StoredConversationTurn, ...] = ()


class ConversationVersionConflict(RuntimeError):
    """The conversation changed after the caller loaded it."""


@dataclass(frozen=True)
class AuditEvent:
    id: str
    occurred_at: datetime
    component: str
    event_type: str
    action: str
    outcome: str
    severity: str
    summary: str
    request_id: str | None = None
    session_id: str | None = None
    principal_id: str | None = None
    organization_id: str | None = None
    model_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    http_status: int | None = None
    duration_ms: float | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class AuditSession:
    session_id: str
    principal_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    event_count: int
    organization_id: str | None = None


@dataclass(frozen=True)
class TraceRun:
    id: str
    principal_id: str
    organization_id: str
    model_id: str
    purpose: str
    question: str
    llm_provider: str
    llm_model: str
    prompt_version: str
    status: str
    started_at: datetime
    request_id: str | None = None
    conversation_id: str | None = None
    question_id: str | None = None
    termination_reason: str | None = None
    answer: str | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    semantic_revision_id: str | None = None


@dataclass(frozen=True)
class SemanticRevision:
    id: str
    model_id: str
    sha256: str
    artifact_path: str
    published_at: datetime
    size_bytes: int
    ossie_version: str | None = None
    discovery_run_id: str | None = None
    published_by: str | None = None


@dataclass(frozen=True)
class TraceSpan:
    id: str
    run_id: str
    sequence: int
    component: str
    kind: str
    name: str
    status: str
    started_at: datetime
    parent_span_id: str | None = None
    completed_at: datetime | None = None
    latency_ms: float | None = None
    input: dict[str, Any] | list[Any] | str | None = None
    output: dict[str, Any] | list[Any] | str | None = None
    attributes: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class EvaluationRun:
    id: str
    principal_id: str
    organization_id: str
    model_id: str
    suite_id: str
    suite_version: str
    status: str
    repetitions: int
    baseline_provider: str
    baseline_model: str
    candidate_provider: str
    candidate_model: str
    max_tool_rounds: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metrics: dict[str, Any] | None = None
    cancel_requested: bool = False


@dataclass(frozen=True)
class EvaluationResult:
    id: str
    evaluation_run_id: str
    question_id: str
    variant: str
    repetition: int
    trace_run_id: str
    accurate: bool
    completed: bool
    metrics: dict[str, Any] | None = None


class MetadataRepository(Protocol):
    """Storage-independent operational metadata interface."""

    def integrity_check(self, *, thorough: bool = False) -> tuple[str, ...]: ...

    def is_healthy(self) -> bool: ...

    def migrate(self) -> None: ...

    def schema_version(self) -> int: ...

    def save_organization(
        self, organization: Organization, slug: str
    ) -> StoredOrganization: ...

    def stored_organization(
        self, organization_id: str
    ) -> StoredOrganization | None: ...

    def organization(self, organization_id: str) -> Organization | None: ...

    def save_principal(self, principal: PrincipalRecord) -> PrincipalRecord: ...

    def principal(self, principal_id: str) -> PrincipalRecord | None: ...

    def add_organization_membership(
        self,
        organization_id: str,
        principal_id: str,
        role: authz.Role,
    ) -> None: ...

    def save_data_source(self, data_source: DataSource) -> DataSource: ...

    def data_source(self, data_source_id: str) -> DataSource | None: ...

    def data_sources_for_organization(
        self, organization_id: str
    ) -> list[DataSource]: ...

    def save_model(
        self,
        model: Model,
        created_by: str,
        status: str = "draft",
    ) -> StoredModel: ...

    def stored_model(self, model_id: str) -> StoredModel | None: ...

    def model(self, model_id: str) -> Model | None: ...

    def models_for_organization(self, organization_id: str) -> list[Model]: ...

    def add_model_grant(
        self,
        model_id: str,
        principal_id: str,
        role: authz.Role,
    ) -> None: ...

    def grants_for_principal(self, principal_id: str) -> list[authz.Grant]: ...

    def create_conversation(
        self,
        model_id: str,
        principal_id: str,
        title: str,
        user_content: str,
        assistant_content: str,
        turn: dict[str, Any] | None = None,
    ) -> StoredConversation: ...

    def conversations_for_principal(
        self,
        model_id: str,
        principal_id: str,
        *,
        include_archived: bool = False,
    ) -> list[StoredConversation]: ...

    def conversation_for_principal(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
    ) -> StoredConversation | None: ...

    def append_conversation_turn(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
        expected_version: int,
        user_content: str,
        assistant_content: str,
        turn: dict[str, Any] | None = None,
    ) -> StoredConversation: ...

    def archive_conversation(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
        *,
        archived: bool,
    ) -> StoredConversation: ...

    def create_trace_run(self, run: TraceRun) -> TraceRun: ...

    def save_semantic_revision(
        self, revision: SemanticRevision
    ) -> SemanticRevision: ...

    def semantic_revision(
        self, revision_id: str
    ) -> SemanticRevision | None: ...

    def latest_semantic_revision(
        self, model_id: str
    ) -> SemanticRevision | None: ...

    def update_trace_run(
        self,
        run_id: str,
        *,
        status: str,
        termination_reason: str | None = None,
        answer: str | None = None,
        completed_at: datetime | None = None,
        duration_ms: float | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        conversation_id: str | None = None,
    ) -> TraceRun: ...

    def append_trace_span(self, span: TraceSpan) -> TraceSpan: ...

    def trace_run(self, run_id: str) -> TraceRun | None: ...

    def trace_spans(self, run_id: str) -> list[TraceSpan]: ...

    def trace_runs(
        self,
        *,
        model_id: str,
        principal_id: str | None = None,
        purpose: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[TraceRun], int]: ...

    def create_evaluation_run(self, run: EvaluationRun) -> EvaluationRun: ...

    def update_evaluation_run(
        self,
        run_id: str,
        *,
        status: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
        cancel_requested: bool | None = None,
    ) -> EvaluationRun: ...

    def evaluation_run(self, run_id: str) -> EvaluationRun | None: ...

    def evaluation_runs(
        self,
        *,
        model_id: str,
        organization_id: str,
        limit: int = 50,
    ) -> list[EvaluationRun]: ...

    def append_evaluation_result(
        self, result: EvaluationResult
    ) -> EvaluationResult: ...

    def evaluation_results(
        self, evaluation_run_id: str
    ) -> list[EvaluationResult]: ...

    def fail_interrupted_evaluations(self) -> int: ...

    def append_audit_event(self, event: AuditEvent) -> AuditEvent: ...

    def audit_events(
        self,
        *,
        principal_id: str | None = None,
        organization_id: str | None = None,
        session_id: str | None = None,
        model_id: str | None = None,
        component: str | None = None,
        event_type: str | None = None,
        outcome: str | None = None,
        severity: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditEvent], int]: ...

    def audit_event(self, event_id: str) -> AuditEvent | None: ...

    def audit_sessions(
        self,
        *,
        principal_id: str | None = None,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditSession]: ...

    def purge_audit_events(self, before: datetime, limit: int = 10_000) -> int: ...
