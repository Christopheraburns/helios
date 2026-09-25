"""SQLite implementation of the operational metadata repository."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
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
from .repository import (
    AuditEvent,
    AuditSession,
    ConversationMessage,
    ConversationVersionConflict,
    PrincipalRecord,
    StoredConversation,
    StoredModel,
    StoredOrganization,
)


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

    def create_conversation(
        self,
        model_id: str,
        principal_id: str,
        title: str,
        user_content: str,
        assistant_content: str,
    ) -> StoredConversation:
        title = title.strip()
        _validate_conversation_text(title, "conversation title", 200)
        _validate_conversation_text(user_content, "user message", 10_000)
        _validate_conversation_text(
            assistant_content, "assistant message", 100_000
        )
        conversation_id = str(uuid.uuid4())
        now = _now()
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id, model_id, principal_id, title, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        conversation_id,
                        model_id,
                        principal_id,
                        title,
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO conversation_messages (
                        id, conversation_id, position, role, content, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            str(uuid.uuid4()),
                            conversation_id,
                            1,
                            "user",
                            user_content,
                            now,
                        ),
                        (
                            str(uuid.uuid4()),
                            conversation_id,
                            2,
                            "assistant",
                            assistant_content,
                            now,
                        ),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        conversation = self.conversation_for_principal(
            conversation_id, model_id, principal_id
        )
        if conversation is None:
            raise RuntimeError("conversation was not persisted")
        return conversation

    def conversations_for_principal(
        self,
        model_id: str,
        principal_id: str,
    ) -> list[StoredConversation]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE model_id = ? AND principal_id = ?
                ORDER BY updated_at DESC, id
                """,
                (model_id, principal_id),
            ).fetchall()
            return [
                self._conversation_from_row(
                    connection, row, include_messages=False
                )
                for row in rows
            ]

    def conversation_for_principal(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
    ) -> StoredConversation | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE id = ? AND model_id = ? AND principal_id = ?
                """,
                (conversation_id, model_id, principal_id),
            ).fetchone()
            if row is None:
                return None
            return self._conversation_from_row(
                connection, row, include_messages=True
            )

    def append_conversation_turn(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
        expected_version: int,
        user_content: str,
        assistant_content: str,
    ) -> StoredConversation:
        _validate_conversation_text(user_content, "user message", 10_000)
        _validate_conversation_text(
            assistant_content, "assistant message", 100_000
        )
        now = _now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT version
                FROM conversations
                WHERE id = ? AND model_id = ? AND principal_id = ?
                """,
                (conversation_id, model_id, principal_id),
            ).fetchone()
            if row is None:
                raise LookupError("conversation not found")
            if row["version"] != expected_version:
                raise ConversationVersionConflict(
                    "conversation changed; reload before sending another message"
                )
            position = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(position), 0) AS position
                    FROM conversation_messages
                    WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()["position"]
            )
            updated = connection.execute(
                """
                UPDATE conversations
                SET version = version + 1, updated_at = ?
                WHERE id = ? AND model_id = ? AND principal_id = ?
                    AND version = ?
                """,
                (
                    now,
                    conversation_id,
                    model_id,
                    principal_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise ConversationVersionConflict(
                    "conversation changed; reload before sending another message"
                )
            connection.executemany(
                """
                INSERT INTO conversation_messages (
                    id, conversation_id, position, role, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        str(uuid.uuid4()),
                        conversation_id,
                        position + 1,
                        "user",
                        user_content,
                        now,
                    ),
                    (
                        str(uuid.uuid4()),
                        conversation_id,
                        position + 2,
                        "assistant",
                        assistant_content,
                        now,
                    ),
                ),
            )
        conversation = self.conversation_for_principal(
            conversation_id, model_id, principal_id
        )
        if conversation is None:
            raise RuntimeError("conversation was not persisted")
        return conversation

    def append_audit_event(self, event: AuditEvent) -> AuditEvent:
        details = event.details or {}
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, occurred_at, request_id, session_id, principal_id,
                    organization_id, model_id, component, event_type, action,
                    resource_type, resource_id, outcome, severity, http_status,
                    duration_ms, summary, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.occurred_at.isoformat(),
                    event.request_id,
                    event.session_id,
                    event.principal_id,
                    event.organization_id,
                    event.model_id,
                    event.component,
                    event.event_type,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.outcome,
                    event.severity,
                    event.http_status,
                    event.duration_ms,
                    event.summary,
                    json.dumps(details, separators=(",", ":"), sort_keys=True),
                ),
            )
        return event

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
    ) -> tuple[list[AuditEvent], int]:
        filters = {
            "principal_id": principal_id,
            "organization_id": organization_id,
            "session_id": session_id,
            "model_id": model_id,
            "component": component,
            "event_type": event_type,
            "outcome": outcome,
            "severity": severity,
        }
        where: list[str] = []
        values: list[object] = []
        for column, value in filters.items():
            if value is not None:
                where.append(f"{column} = ?")
                values.append(value)
        if occurred_from is not None:
            where.append("occurred_at >= ?")
            values.append(occurred_from.isoformat())
        if occurred_to is not None:
            where.append("occurred_at <= ?")
            values.append(occurred_to.isoformat())
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS count FROM audit_events{clause}",
                    values,
                ).fetchone()["count"]
            )
            rows = connection.execute(
                f"""
                SELECT * FROM audit_events
                {clause}
                ORDER BY occurred_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*values, limit, offset],
            ).fetchall()
            return [_audit_event(row) for row in rows], total

    def audit_event(self, event_id: str) -> AuditEvent | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM audit_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            return _audit_event(row) if row else None

    def audit_sessions(
        self,
        *,
        principal_id: str | None = None,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditSession]:
        where = ["session_id IS NOT NULL", "principal_id IS NOT NULL"]
        values: list[object] = []
        if principal_id is not None:
            where.append("principal_id = ?")
            values.append(principal_id)
        if organization_id is not None:
            where.append("organization_id = ?")
            values.append(organization_id)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT session_id, principal_id,
                       MIN(occurred_at) AS first_seen_at,
                       MAX(occurred_at) AS last_seen_at,
                       COUNT(*) AS event_count,
                       MAX(organization_id) AS organization_id
                FROM audit_events
                WHERE {' AND '.join(where)}
                GROUP BY session_id, principal_id
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                [*values, limit],
            ).fetchall()
            return [
                AuditSession(
                    session_id=row["session_id"],
                    principal_id=row["principal_id"],
                    first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
                    last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
                    event_count=row["event_count"],
                    organization_id=row["organization_id"],
                )
                for row in rows
            ]

    def purge_audit_events(
        self,
        before: datetime,
        limit: int = 10_000,
    ) -> int:
        with self._connection() as connection:
            deleted = connection.execute(
                """
                DELETE FROM audit_events
                WHERE id IN (
                    SELECT id FROM audit_events
                    WHERE occurred_at < ?
                    ORDER BY occurred_at
                    LIMIT ?
                )
                """,
                (before.isoformat(), limit),
            )
            return deleted.rowcount

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

    def _conversation_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        include_messages: bool,
    ) -> StoredConversation:
        messages: tuple[ConversationMessage, ...] = ()
        if include_messages:
            messages = tuple(
                ConversationMessage(
                    id=message["id"],
                    conversation_id=message["conversation_id"],
                    position=message["position"],
                    role=message["role"],
                    content=message["content"],
                    created_at=datetime.fromisoformat(message["created_at"]),
                )
                for message in connection.execute(
                    """
                    SELECT *
                    FROM conversation_messages
                    WHERE conversation_id = ?
                    ORDER BY position
                    """,
                    (row["id"],),
                )
            )
        return StoredConversation(
            id=row["id"],
            model_id=row["model_id"],
            principal_id=row["principal_id"],
            title=row["title"],
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            messages=messages,
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


def _audit_event(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        request_id=row["request_id"],
        session_id=row["session_id"],
        principal_id=row["principal_id"],
        organization_id=row["organization_id"],
        model_id=row["model_id"],
        component=row["component"],
        event_type=row["event_type"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        outcome=row["outcome"],
        severity=row["severity"],
        http_status=row["http_status"],
        duration_ms=row["duration_ms"],
        summary=row["summary"],
        details=json.loads(row["details_json"]),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_conversation_text(
    value: str,
    label: str,
    maximum: int,
) -> None:
    if not value or not value.strip():
        raise ValueError(f"{label} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{label} must not exceed {maximum} characters")
