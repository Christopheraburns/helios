"""SQLite implementation of the operational metadata repository."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from helios_core import authz
from helios_core.domain import (
    DataSource,
    DataSourceReference,
    Model,
    Organization,
)

from .migrations import MIGRATIONS
from .repository import PrincipalRecord, StoredModel, StoredOrganization


def default_database_path() -> str:
    root = os.environ.get("HELIOS_ROOT") or os.path.join(
        os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"
    )
    return os.environ.get("HELIOS_METADATA_DB") or os.path.join(
        root, "state", "helios.db"
    )


class SQLiteMetadataRepository:
    """Transactional SQLite repository with one connection per operation."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = str(path or default_database_path())

    def migrate(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                )
            }
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                applied_at = _now()
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations (version, applied_at) "
                    f"VALUES ({int(version)}, '{applied_at}');\n"
                    "COMMIT;"
                )
                try:
                    connection.executescript(script)
                except Exception:
                    connection.rollback()
                    raise

    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM schema_migrations"
            ).fetchone()
            return int(row["version"])

    def save_organization(
        self, organization: Organization, slug: str
    ) -> StoredOrganization:
        if not slug or not slug.strip():
            raise ValueError("organization slug must not be empty")
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO organizations (id, name, slug)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        slug = excluded.slug
                    """,
                    (organization.id, organization.name, slug),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        return StoredOrganization(organization, slug)

    def stored_organization(
        self, organization_id: str
    ) -> StoredOrganization | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, name, slug FROM organizations WHERE id = ?",
                (organization_id,),
            ).fetchone()
            if row is None:
                return None
            members = tuple(
                item["principal_id"]
                for item in connection.execute(
                    """
                    SELECT DISTINCT principal_id
                    FROM organization_memberships
                    WHERE organization_id = ?
                    ORDER BY principal_id
                    """,
                    (organization_id,),
                )
            )
            organization = Organization(
                row["id"], row["name"], member_ids=members
            )
            return StoredOrganization(organization, row["slug"])

    def organization(self, organization_id: str) -> Organization | None:
        stored = self.stored_organization(organization_id)
        return stored.organization if stored else None

    def save_principal(self, principal: PrincipalRecord) -> PrincipalRecord:
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO principals (
                        id, external_identity, display_name, kind
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        external_identity = excluded.external_identity,
                        display_name = excluded.display_name,
                        kind = excluded.kind
                    """,
                    (
                        principal.id,
                        principal.external_identity,
                        principal.display_name,
                        principal.kind.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        return principal

    def principal(self, principal_id: str) -> PrincipalRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, external_identity, display_name, kind
                FROM principals
                WHERE id = ?
                """,
                (principal_id,),
            ).fetchone()
            return _principal(row) if row else None

    def add_organization_membership(
        self,
        organization_id: str,
        principal_id: str,
        role: authz.Role,
    ) -> None:
        if role is not authz.Role.ORG_ADMIN:
            raise ValueError("organization memberships currently support org_admin")
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO organization_memberships (
                        organization_id, principal_id, role
                    ) VALUES (?, ?, ?)
                    """,
                    (organization_id, principal_id, role.value),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc

    def save_data_source(self, data_source: DataSource) -> DataSource:
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO data_sources (
                        id, organization_id, name, connector, connection_ref
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        connector = excluded.connector,
                        connection_ref = excluded.connection_ref
                    WHERE data_sources.organization_id = excluded.organization_id
                    """,
                    (
                        data_source.id,
                        data_source.organization_id,
                        data_source.name,
                        data_source.connector,
                        data_source.connection_ref,
                    ),
                )
                row = connection.execute(
                    "SELECT organization_id FROM data_sources WHERE id = ?",
                    (data_source.id,),
                ).fetchone()
                if row and row["organization_id"] != data_source.organization_id:
                    raise ValueError(
                        "cannot move a data source between organizations"
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        return data_source

    def data_source(self, data_source_id: str) -> DataSource | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, organization_id, name, connector, connection_ref
                FROM data_sources
                WHERE id = ?
                """,
                (data_source_id,),
            ).fetchone()
            return _data_source(row) if row else None

    def data_sources_for_organization(
        self, organization_id: str
    ) -> list[DataSource]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, organization_id, name, connector, connection_ref
                FROM data_sources
                WHERE organization_id = ?
                ORDER BY lower(name), id
                """,
                (organization_id,),
            ).fetchall()
            return [_data_source(row) for row in rows]

    def save_model(
        self,
        model: Model,
        created_by: str,
        status: str = "draft",
    ) -> StoredModel:
        if not status or not status.strip():
            raise ValueError("model status must not be empty")
        now = _now()
        with self._connection() as connection:
            self._validate_model_references(connection, model)
            try:
                connection.execute(
                    """
                    INSERT INTO models (
                        id, organization_id, name, description, status,
                        created_by, created_at, updated_at,
                        glossary_id, semantic_model_id, ontology_id,
                        version_ids_json, discovery_run_ids_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        glossary_id = excluded.glossary_id,
                        semantic_model_id = excluded.semantic_model_id,
                        ontology_id = excluded.ontology_id,
                        version_ids_json = excluded.version_ids_json,
                        discovery_run_ids_json = excluded.discovery_run_ids_json
                    WHERE models.organization_id = excluded.organization_id
                    """,
                    (
                        model.id,
                        model.organization_id,
                        model.name,
                        model.description,
                        status,
                        created_by,
                        now,
                        now,
                        model.glossary_id,
                        model.semantic_model_id,
                        model.ontology_id,
                        json.dumps(list(model.version_ids)),
                        json.dumps(list(model.discovery_run_ids)),
                    ),
                )
                owner = connection.execute(
                    "SELECT organization_id FROM models WHERE id = ?",
                    (model.id,),
                ).fetchone()
                if owner and owner["organization_id"] != model.organization_id:
                    raise ValueError("cannot move a model between organizations")
                connection.execute(
                    "DELETE FROM model_data_sources WHERE model_id = ?",
                    (model.id,),
                )
                connection.executemany(
                    """
                    INSERT INTO model_data_sources (
                        model_id, data_source_id, selected_assets_json
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (
                            model.id,
                            reference.data_source_id,
                            json.dumps(list(reference.selected_assets)),
                        )
                        for reference in model.data_sources
                    ],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        stored = self.stored_model(model.id)
        if stored is None:  # defensive: the transaction above must create it
            raise RuntimeError(f"model {model.id!r} was not persisted")
        return stored

    def stored_model(self, model_id: str) -> StoredModel | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM models WHERE id = ?", (model_id,)
            ).fetchone()
            if row is None:
                return None
            model = self._model_from_row(connection, row)
            return StoredModel(
                model=model,
                status=row["status"],
                created_by=row["created_by"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def model(self, model_id: str) -> Model | None:
        stored = self.stored_model(model_id)
        return stored.model if stored else None

    def models_for_organization(self, organization_id: str) -> list[Model]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM models
                WHERE organization_id = ?
                ORDER BY lower(name), id
                """,
                (organization_id,),
            ).fetchall()
            return [self._model_from_row(connection, row) for row in rows]

    def add_model_grant(
        self,
        model_id: str,
        principal_id: str,
        role: authz.Role,
    ) -> None:
        if role is authz.Role.ORG_ADMIN:
            raise ValueError("org_admin must be an organization membership")
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO model_memberships (
                        model_id, principal_id, role
                    ) VALUES (?, ?, ?)
                    """,
                    (model_id, principal_id, role.value),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc

    def grants_for_principal(self, principal_id: str) -> list[authz.Grant]:
        grants: list[authz.Grant] = []
        with self._connection() as connection:
            for row in connection.execute(
                """
                SELECT organization_id, role
                FROM organization_memberships
                WHERE principal_id = ?
                ORDER BY organization_id, role
                """,
                (principal_id,),
            ):
                resource = authz.Resource(
                    "organization",
                    row["organization_id"],
                    row["organization_id"],
                )
                grants.append(
                    authz.Grant(principal_id, authz.Role(row["role"]), resource)
                )
            for row in connection.execute(
                """
                SELECT mm.model_id, mm.role, m.organization_id
                FROM model_memberships mm
                JOIN models m ON m.id = mm.model_id
                WHERE mm.principal_id = ?
                ORDER BY m.organization_id, mm.model_id, mm.role
                """,
                (principal_id,),
            ):
                resource = authz.Resource(
                    "model", row["model_id"], row["organization_id"]
                )
                grants.append(
                    authz.Grant(principal_id, authz.Role(row["role"]), resource)
                )
        return grants

    def _validate_model_references(
        self, connection: sqlite3.Connection, model: Model
    ) -> None:
        organization = connection.execute(
            "SELECT 1 FROM organizations WHERE id = ?",
            (model.organization_id,),
        ).fetchone()
        if organization is None:
            raise ValueError(f"unknown organization {model.organization_id!r}")
        for reference in model.data_sources:
            row = connection.execute(
                "SELECT organization_id FROM data_sources WHERE id = ?",
                (reference.data_source_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"unknown data source {reference.data_source_id!r}"
                )
            if row["organization_id"] != model.organization_id:
                raise ValueError(
                    f"data source {reference.data_source_id!r} belongs to "
                    "another organization"
                )

    def _model_from_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> Model:
        references = tuple(
            DataSourceReference(
                reference["data_source_id"],
                tuple(json.loads(reference["selected_assets_json"])),
            )
            for reference in connection.execute(
                """
                SELECT data_source_id, selected_assets_json
                FROM model_data_sources
                WHERE model_id = ?
                ORDER BY data_source_id
                """,
                (row["id"],),
            )
        )
        return Model(
            id=row["id"],
            organization_id=row["organization_id"],
            name=row["name"],
            description=row["description"],
            data_sources=references,
            version_ids=tuple(json.loads(row["version_ids_json"])),
            discovery_run_ids=tuple(
                json.loads(row["discovery_run_ids_json"])
            ),
            glossary_id=row["glossary_id"],
            semantic_model_id=row["semantic_model_id"],
            ontology_id=row["ontology_id"],
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _principal(row: sqlite3.Row) -> PrincipalRecord:
    return PrincipalRecord(
        id=row["id"],
        external_identity=row["external_identity"],
        display_name=row["display_name"],
        kind=authz.PrincipalKind(row["kind"]),
    )


def _data_source(row: sqlite3.Row) -> DataSource:
    return DataSource(
        id=row["id"],
        organization_id=row["organization_id"],
        name=row["name"],
        connector=row["connector"],
        connection_ref=row["connection_ref"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
