"""Repository contracts and records for Helios operational metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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
