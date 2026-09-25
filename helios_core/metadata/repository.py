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
class StoredConversation:
    id: str
    model_id: str
    principal_id: str
    title: str
    version: int
    created_at: datetime
    updated_at: datetime
    messages: tuple[ConversationMessage, ...] = ()


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


class MetadataRepository(Protocol):
    """Storage-independent operational metadata interface."""

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
    ) -> StoredConversation: ...

    def conversations_for_principal(
        self,
        model_id: str,
        principal_id: str,
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
    ) -> StoredConversation: ...

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
