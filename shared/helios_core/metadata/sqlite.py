"""SQLite implementation of the operational metadata repository."""

from __future__ import annotations

import json
import logging
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
    EvaluationResult,
    EvaluationRun,
    PrincipalRecord,
    SemanticRevision,
    StoredConversation,
    StoredConversationTurn,
    StoredModel,
    StoredOrganization,
    TraceRun,
    TraceSpan,
)

LOGGER = logging.getLogger(__name__)
SUPPORTED_JOURNAL_MODES = frozenset({"DELETE", "TRUNCATE", "PERSIST", "WAL"})


def default_database_path() -> str:
    root = os.environ.get("HELIOS_ROOT") or os.path.join(
        os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"
    )
    return os.environ.get("HELIOS_METADATA_DB") or os.path.join(root, "state", "helios.db")


class SQLiteMetadataRepository:
    """Transactional SQLite repository with one connection per operation."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = str(path or default_database_path())

    def migrate(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            if self.path != ":memory:":
                requested_mode = (
                    os.environ.get("HELIOS_SQLITE_JOURNAL_MODE", "DELETE").strip().upper()
                )
                if requested_mode not in SUPPORTED_JOURNAL_MODES:
                    raise ValueError(
                        "HELIOS_SQLITE_JOURNAL_MODE must be one of "
                        + ", ".join(sorted(SUPPORTED_JOURNAL_MODES))
                    )
                actual_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
                if actual_mode != requested_mode:
                    actual_mode = str(
                        connection.execute(f"PRAGMA journal_mode = {requested_mode}").fetchone()[0]
                    ).upper()
                if actual_mode != requested_mode:
                    raise RuntimeError(
                        "SQLite journal mode could not be set to "
                        f"{requested_mode}; active mode is {actual_mode}"
                    )
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
                for row in connection.execute("SELECT version FROM schema_migrations")
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

    def integrity_check(self, *, thorough: bool = False) -> tuple[str, ...]:
        """Return SQLite integrity findings without modifying the database."""
        pragma = "integrity_check" if thorough else "quick_check(1)"
        try:
            if self.path == ":memory:":
                with self._connection() as connection:
                    rows = connection.execute(f"PRAGMA {pragma}").fetchall()
            else:
                uri = Path(self.path).resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=10) as connection:
                    rows = connection.execute(f"PRAGMA {pragma}").fetchall()
            return tuple(str(row[0]) for row in rows)
        except sqlite3.Error as exc:
            return (f"{type(exc).__name__}: {exc}",)

    def is_healthy(self) -> bool:
        return self.integrity_check() == ("ok",)

    def ping(self) -> tuple[str, ...]:
        """("ok",) if the database opens and answers a query. For health endpoints:
        unlike integrity_check, the cost does not grow with the size of the file."""
        try:
            if self.path == ":memory:":
                with self._connection() as connection:
                    connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchall()
            else:
                uri = Path(self.path).resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=10) as connection:
                    connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchall()
            return ("ok",)
        except sqlite3.Error as exc:
            return (f"{type(exc).__name__}: {exc}",)

    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])

    def save_organization(self, organization: Organization, slug: str) -> StoredOrganization:
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

    def stored_organization(self, organization_id: str) -> StoredOrganization | None:
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
            organization = Organization(row["id"], row["name"], member_ids=members)
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
                        id, organization_id, name, connector, connection_ref,
                        description, scope_json, crawl_json, updated_at, updated_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        connector = excluded.connector,
                        connection_ref = excluded.connection_ref,
                        description = excluded.description,
                        scope_json = excluded.scope_json,
                        crawl_json = excluded.crawl_json,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                    WHERE data_sources.organization_id = excluded.organization_id
                    """,
                    (
                        data_source.id,
                        data_source.organization_id,
                        data_source.name,
                        data_source.connector,
                        data_source.connection_ref,
                        data_source.description,
                        json.dumps(data_source.scope, sort_keys=True),
                        json.dumps(data_source.crawl, sort_keys=True),
                        data_source.updated_at,
                        data_source.updated_by,
                    ),
                )
                row = connection.execute(
                    "SELECT organization_id FROM data_sources WHERE id = ?",
                    (data_source.id,),
                ).fetchone()
                if row and row["organization_id"] != data_source.organization_id:
                    raise ValueError("cannot move a data source between organizations")
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        return data_source

    def data_source(self, data_source_id: str) -> DataSource | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, organization_id, name, connector, connection_ref,
                       description, scope_json, crawl_json, updated_at, updated_by
                FROM data_sources
                WHERE id = ?
                """,
                (data_source_id,),
            ).fetchone()
            return _data_source(row) if row else None

    def delete_data_source(self, data_source_id: str) -> bool:
        """Delete a data source; ValueError if a model still uses it."""
        with self._connection() as connection:
            try:
                cursor = connection.execute(
                    "DELETE FROM data_sources WHERE id = ?", (data_source_id,)
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("the data source is used by a model") from exc
            return cursor.rowcount > 0

    def data_sources_for_organization(self, organization_id: str) -> list[DataSource]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, organization_id, name, connector, connection_ref,
                       description, scope_json, crawl_json, updated_at, updated_by
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
            row = connection.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
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
                grants.append(authz.Grant(principal_id, authz.Role(row["role"]), resource))
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
                resource = authz.Resource("model", row["model_id"], row["organization_id"])
                grants.append(authz.Grant(principal_id, authz.Role(row["role"]), resource))
        return grants

    def create_conversation(
        self,
        model_id: str,
        principal_id: str,
        title: str,
        user_content: str,
        assistant_content: str,
        turn: dict | None = None,
    ) -> StoredConversation:
        title = title.strip()
        _validate_conversation_text(title, "conversation title", 200)
        _validate_conversation_text(user_content, "user message", 10_000)
        _validate_conversation_text(assistant_content, "assistant message", 100_000)
        conversation_id = str(uuid.uuid4())
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
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
                            user_message_id,
                            conversation_id,
                            1,
                            "user",
                            user_content,
                            now,
                        ),
                        (
                            assistant_message_id,
                            conversation_id,
                            2,
                            "assistant",
                            assistant_content,
                            now,
                        ),
                    ),
                )
                if turn is not None:
                    _insert_conversation_turn(
                        connection,
                        conversation_id,
                        user_message_id,
                        assistant_message_id,
                        turn,
                        now,
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        conversation = self.conversation_for_principal(conversation_id, model_id, principal_id)
        if conversation is None:
            raise RuntimeError("conversation was not persisted")
        return conversation

    def conversations_for_principal(
        self,
        model_id: str,
        principal_id: str,
        *,
        include_archived: bool = False,
    ) -> list[StoredConversation]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE model_id = ? AND principal_id = ?
                    AND (? OR archived_at IS NULL)
                ORDER BY updated_at DESC, id
                """,
                (model_id, principal_id, include_archived),
            ).fetchall()
            return [
                self._conversation_from_row(connection, row, include_messages=False) for row in rows
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
            return self._conversation_from_row(connection, row, include_messages=True)

    def append_conversation_turn(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
        expected_version: int,
        user_content: str,
        assistant_content: str,
        turn: dict | None = None,
    ) -> StoredConversation:
        _validate_conversation_text(user_content, "user message", 10_000)
        _validate_conversation_text(assistant_content, "assistant message", 100_000)
        now = _now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT version, archived_at
                FROM conversations
                WHERE id = ? AND model_id = ? AND principal_id = ?
                """,
                (conversation_id, model_id, principal_id),
            ).fetchone()
            if row is None:
                raise LookupError("conversation not found")
            if row["archived_at"] is not None:
                raise ConversationVersionConflict(
                    "archived conversations cannot accept new messages"
                )
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
            user_message_id = str(uuid.uuid4())
            assistant_message_id = str(uuid.uuid4())
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
                        user_message_id,
                        conversation_id,
                        position + 1,
                        "user",
                        user_content,
                        now,
                    ),
                    (
                        assistant_message_id,
                        conversation_id,
                        position + 2,
                        "assistant",
                        assistant_content,
                        now,
                    ),
                ),
            )
            if turn is not None:
                _insert_conversation_turn(
                    connection,
                    conversation_id,
                    user_message_id,
                    assistant_message_id,
                    turn,
                    now,
                )
        conversation = self.conversation_for_principal(conversation_id, model_id, principal_id)
        if conversation is None:
            raise RuntimeError("conversation was not persisted")
        return conversation

    def archive_conversation(
        self,
        conversation_id: str,
        model_id: str,
        principal_id: str,
        *,
        archived: bool,
    ) -> StoredConversation:
        now = _now()
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE conversations
                SET archived_at = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE id = ? AND model_id = ? AND principal_id = ?
                """,
                (
                    now if archived else None,
                    now,
                    conversation_id,
                    model_id,
                    principal_id,
                ),
            )
            if updated.rowcount != 1:
                raise LookupError("conversation not found")
        conversation = self.conversation_for_principal(conversation_id, model_id, principal_id)
        if conversation is None:
            raise RuntimeError("conversation was not persisted")
        return conversation

    def create_trace_run(self, run: TraceRun) -> TraceRun:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_trace_runs (
                    id, request_id, conversation_id, principal_id,
                    organization_id, model_id, purpose, question_id,
                    question, llm_provider, llm_model, prompt_version,
                    status, termination_reason, answer, started_at,
                    completed_at, duration_ms, tokens_in, tokens_out,
                    semantic_revision_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run.id,
                    run.request_id,
                    run.conversation_id,
                    run.principal_id,
                    run.organization_id,
                    run.model_id,
                    run.purpose,
                    run.question_id,
                    run.question,
                    run.llm_provider,
                    run.llm_model,
                    run.prompt_version,
                    run.status,
                    run.termination_reason,
                    run.answer,
                    run.started_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.duration_ms,
                    run.tokens_in,
                    run.tokens_out,
                    run.semantic_revision_id,
                ),
            )
        return run

    def save_semantic_revision(self, revision: SemanticRevision) -> SemanticRevision:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO semantic_artifact_revisions (
                    id, model_id, sha256, artifact_path, ossie_version,
                    discovery_run_id, published_at, published_by, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision.id,
                    revision.model_id,
                    revision.sha256,
                    revision.artifact_path,
                    revision.ossie_version,
                    revision.discovery_run_id,
                    revision.published_at.isoformat(),
                    revision.published_by,
                    revision.size_bytes,
                ),
            )
            row = connection.execute(
                "SELECT * FROM semantic_artifact_revisions WHERE id = ?",
                (revision.id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("semantic revision was not persisted")
        stored = _semantic_revision(row)
        if (
            stored.model_id != revision.model_id
            or stored.sha256 != revision.sha256
            or stored.artifact_path != revision.artifact_path
            or stored.ossie_version != revision.ossie_version
            or stored.size_bytes != revision.size_bytes
        ):
            raise RuntimeError("semantic revision metadata is immutable")
        return stored

    def semantic_revision(self, revision_id: str) -> SemanticRevision | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM semantic_artifact_revisions WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
        return _semantic_revision(row) if row else None

    def latest_semantic_revision(self, model_id: str) -> SemanticRevision | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM semantic_artifact_revisions
                WHERE model_id = ?
                ORDER BY published_at DESC, id DESC
                LIMIT 1
                """,
                (model_id,),
            ).fetchone()
        return _semantic_revision(row) if row else None

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
    ) -> TraceRun:
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE agent_trace_runs
                SET status = ?, termination_reason = ?, answer = ?,
                    completed_at = ?, duration_ms = ?, tokens_in = ?,
                    tokens_out = ?,
                    conversation_id = COALESCE(?, conversation_id)
                WHERE id = ?
                """,
                (
                    status,
                    termination_reason,
                    answer,
                    completed_at.isoformat() if completed_at else None,
                    duration_ms,
                    tokens_in,
                    tokens_out,
                    conversation_id,
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise LookupError("trace run not found")
        result = self.trace_run(run_id)
        if result is None:
            raise RuntimeError("trace run update was not persisted")
        return result

    def append_trace_span(self, span: TraceSpan) -> TraceSpan:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_trace_spans (
                    id, run_id, parent_span_id, sequence, component, kind,
                    name, status, started_at, completed_at, latency_ms,
                    input_json, output_json, attributes_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.id,
                    span.run_id,
                    span.parent_span_id,
                    span.sequence,
                    span.component,
                    span.kind,
                    span.name,
                    span.status,
                    span.started_at.isoformat(),
                    span.completed_at.isoformat() if span.completed_at else None,
                    span.latency_ms,
                    _bounded_json(span.input, 250_000),
                    _bounded_json(span.output, 250_000),
                    _bounded_json(span.attributes or {}, 100_000),
                    span.error[:10_000] if span.error else None,
                ),
            )
        return span

    def trace_run(self, run_id: str) -> TraceRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_trace_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            return _trace_run(row) if row else None

    def trace_spans(self, run_id: str) -> list[TraceSpan]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_trace_spans
                WHERE run_id = ?
                ORDER BY sequence, component, id
                """,
                (run_id,),
            ).fetchall()
            return [_trace_span(row) for row in rows]

    def trace_runs(
        self,
        *,
        model_id: str,
        principal_id: str | None = None,
        purpose: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[TraceRun], int]:
        where = ["model_id = ?"]
        values: list[object] = [model_id]
        if principal_id is not None:
            where.append("principal_id = ?")
            values.append(principal_id)
        if purpose is not None:
            where.append("purpose = ?")
            values.append(purpose)
        if status is not None:
            where.append("status = ?")
            values.append(status)
        clause = " AND ".join(where)
        with self._connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS count FROM agent_trace_runs WHERE {clause}",
                    values,
                ).fetchone()["count"]
            )
            rows = connection.execute(
                f"""
                SELECT * FROM agent_trace_runs
                WHERE {clause}
                ORDER BY started_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*values, limit, offset],
            ).fetchall()
            return [_trace_run(row) for row in rows], total

    def create_evaluation_run(self, run: EvaluationRun) -> EvaluationRun:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs (
                    id, principal_id, organization_id, model_id, suite_id,
                    suite_version, status, repetitions, baseline_provider,
                    baseline_model, candidate_provider, candidate_model,
                    max_tool_rounds, created_at, started_at, completed_at,
                    error, metrics_json, cancel_requested
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.principal_id,
                    run.organization_id,
                    run.model_id,
                    run.suite_id,
                    run.suite_version,
                    run.status,
                    run.repetitions,
                    run.baseline_provider,
                    run.baseline_model,
                    run.candidate_provider,
                    run.candidate_model,
                    run.max_tool_rounds,
                    run.created_at.isoformat(),
                    run.started_at.isoformat() if run.started_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.error,
                    _bounded_json(run.metrics or {}, 250_000),
                    int(run.cancel_requested),
                ),
            )
        return run

    def update_evaluation_run(
        self,
        run_id: str,
        *,
        status: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error: str | None = None,
        metrics: dict | None = None,
        cancel_requested: bool | None = None,
    ) -> EvaluationRun:
        with self._connection() as connection:
            current = connection.execute(
                "SELECT * FROM evaluation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if current is None:
                raise LookupError("evaluation run not found")
            connection.execute(
                """
                UPDATE evaluation_runs
                SET status = ?, started_at = ?, completed_at = ?, error = ?,
                    metrics_json = ?, cancel_requested = ?
                WHERE id = ?
                """,
                (
                    status,
                    started_at.isoformat() if started_at else current["started_at"],
                    (completed_at.isoformat() if completed_at else current["completed_at"]),
                    error,
                    _bounded_json(
                        metrics if metrics is not None else json.loads(current["metrics_json"]),
                        250_000,
                    ),
                    (
                        int(cancel_requested)
                        if cancel_requested is not None
                        else current["cancel_requested"]
                    ),
                    run_id,
                ),
            )
        result = self.evaluation_run(run_id)
        if result is None:
            raise RuntimeError("evaluation run update was not persisted")
        return result

    def evaluation_run(self, run_id: str) -> EvaluationRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            return _evaluation_run(row) if row else None

    def evaluation_runs(
        self,
        *,
        model_id: str,
        organization_id: str,
        limit: int = 50,
    ) -> list[EvaluationRun]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_runs
                WHERE model_id = ? AND organization_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (model_id, organization_id, limit),
            ).fetchall()
            return [_evaluation_run(row) for row in rows]

    def append_evaluation_result(self, result: EvaluationResult) -> EvaluationResult:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_results (
                    id, evaluation_run_id, question_id, variant, repetition,
                    trace_run_id, accurate, completed, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.id,
                    result.evaluation_run_id,
                    result.question_id,
                    result.variant,
                    result.repetition,
                    result.trace_run_id,
                    int(result.accurate),
                    int(result.completed),
                    _bounded_json(result.metrics or {}, 100_000),
                ),
            )
        return result

    def evaluation_results(self, evaluation_run_id: str) -> list[EvaluationResult]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_results
                WHERE evaluation_run_id = ?
                ORDER BY question_id, variant, repetition
                """,
                (evaluation_run_id,),
            ).fetchall()
            return [_evaluation_result(row) for row in rows]

    def fail_interrupted_evaluations(self) -> int:
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE evaluation_runs
                SET status = 'failed',
                    completed_at = ?,
                    error = 'The API restarted before this evaluation completed.'
                WHERE status IN ('queued', 'running')
                """,
                (_now(),),
            )
            return updated.rowcount

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
                WHERE {" AND ".join(where)}
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

    def _validate_model_references(self, connection: sqlite3.Connection, model: Model) -> None:
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
                raise ValueError(f"unknown data source {reference.data_source_id!r}")
            if row["organization_id"] != model.organization_id:
                raise ValueError(
                    f"data source {reference.data_source_id!r} belongs to another organization"
                )

    def _model_from_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Model:
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
            discovery_run_ids=tuple(json.loads(row["discovery_run_ids_json"])),
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
        turns: tuple[StoredConversationTurn, ...] = ()
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
            turns = tuple(
                StoredConversationTurn(
                    id=turn["id"],
                    conversation_id=turn["conversation_id"],
                    user_message_id=turn["user_message_id"],
                    assistant_message_id=turn["assistant_message_id"],
                    request_id=turn["request_id"],
                    tool_trace=tuple(json.loads(turn["tool_trace_json"])),
                    query_result=(
                        json.loads(turn["query_result_json"]) if turn["query_result_json"] else None
                    ),
                    provenance=json.loads(turn["provenance_json"]),
                    trace_run_id=turn["trace_run_id"],
                    created_at=datetime.fromisoformat(turn["created_at"]),
                )
                for turn in connection.execute(
                    """
                    SELECT *
                    FROM conversation_turns
                    WHERE conversation_id = ?
                    ORDER BY created_at, id
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
            archived_at=(
                datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None
            ),
            messages=messages,
            turns=turns,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
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
        description=row["description"],
        scope=json.loads(row["scope_json"] or "{}"),
        crawl=json.loads(row["crawl_json"] or "{}"),
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
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


def _trace_run(row: sqlite3.Row) -> TraceRun:
    return TraceRun(
        id=row["id"],
        request_id=row["request_id"],
        conversation_id=row["conversation_id"],
        principal_id=row["principal_id"],
        organization_id=row["organization_id"],
        model_id=row["model_id"],
        purpose=row["purpose"],
        question_id=row["question_id"],
        question=row["question"],
        llm_provider=row["llm_provider"],
        llm_model=row["llm_model"],
        prompt_version=row["prompt_version"],
        status=row["status"],
        termination_reason=row["termination_reason"],
        answer=row["answer"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        duration_ms=row["duration_ms"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        semantic_revision_id=row["semantic_revision_id"],
    )


def _semantic_revision(row: sqlite3.Row) -> SemanticRevision:
    return SemanticRevision(
        id=row["id"],
        model_id=row["model_id"],
        sha256=row["sha256"],
        artifact_path=row["artifact_path"],
        ossie_version=row["ossie_version"],
        discovery_run_id=row["discovery_run_id"],
        published_at=datetime.fromisoformat(row["published_at"]),
        published_by=row["published_by"],
        size_bytes=row["size_bytes"],
    )


def _trace_span(row: sqlite3.Row) -> TraceSpan:
    return TraceSpan(
        id=row["id"],
        run_id=row["run_id"],
        parent_span_id=row["parent_span_id"],
        sequence=row["sequence"],
        component=row["component"],
        kind=row["kind"],
        name=row["name"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        latency_ms=row["latency_ms"],
        input=json.loads(row["input_json"]),
        output=json.loads(row["output_json"]),
        attributes=json.loads(row["attributes_json"]),
        error=row["error"],
    )


def _evaluation_run(row: sqlite3.Row) -> EvaluationRun:
    return EvaluationRun(
        id=row["id"],
        principal_id=row["principal_id"],
        organization_id=row["organization_id"],
        model_id=row["model_id"],
        suite_id=row["suite_id"],
        suite_version=row["suite_version"],
        status=row["status"],
        repetitions=row["repetitions"],
        baseline_provider=row["baseline_provider"],
        baseline_model=row["baseline_model"],
        candidate_provider=row["candidate_provider"],
        candidate_model=row["candidate_model"],
        max_tool_rounds=row["max_tool_rounds"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        error=row["error"],
        metrics=json.loads(row["metrics_json"]),
        cancel_requested=bool(row["cancel_requested"]),
    )


def _evaluation_result(row: sqlite3.Row) -> EvaluationResult:
    return EvaluationResult(
        id=row["id"],
        evaluation_run_id=row["evaluation_run_id"],
        question_id=row["question_id"],
        variant=row["variant"],
        repetition=row["repetition"],
        trace_run_id=row["trace_run_id"],
        accurate=bool(row["accurate"]),
        completed=bool(row["completed"]),
        metrics=json.loads(row["metrics_json"]),
    )


def _insert_conversation_turn(
    connection: sqlite3.Connection,
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    turn: dict,
    created_at: str,
) -> None:
    tool_trace = turn.get("tool_trace")
    query_result = turn.get("query_result")
    provenance = turn.get("provenance") or {}
    if not isinstance(tool_trace, list):
        raise ValueError("conversation tool trace must be a list")
    if query_result is not None and not isinstance(query_result, dict):
        raise ValueError("conversation query result must be an object")
    if not isinstance(provenance, dict):
        raise ValueError("conversation provenance must be an object")
    request_id = turn.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise ValueError("conversation request ID must be a string")
    trace_run_id = turn.get("trace_run_id")
    if trace_run_id is not None and not isinstance(trace_run_id, str):
        raise ValueError("conversation trace run ID must be a string")
    connection.execute(
        """
        INSERT INTO conversation_turns (
            id, conversation_id, user_message_id, assistant_message_id,
            request_id, tool_trace_json, query_result_json,
            provenance_json, trace_run_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            conversation_id,
            user_message_id,
            assistant_message_id,
            request_id,
            _conversation_json(tool_trace, "tool trace", 1_000_000),
            (
                _conversation_json(query_result, "query result", 1_000_000)
                if query_result is not None
                else None
            ),
            _conversation_json(provenance, "provenance", 100_000),
            trace_run_id,
            created_at,
        ),
    )
    if trace_run_id:
        connection.execute(
            """
            UPDATE agent_trace_runs
            SET conversation_id = ?
            WHERE id = ?
            """,
            (conversation_id, trace_run_id),
        )


def _conversation_json(value: object, label: str, maximum: int) -> str:
    encoded = json.dumps(value, separators=(",", ":"), default=str)
    if len(encoded.encode()) > maximum:
        raise ValueError(f"conversation {label} exceeds the persistence limit")
    return encoded


def _bounded_json(value: object, maximum: int) -> str:
    encoded = json.dumps(
        value if value is not None else {},
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    if len(encoded.encode()) <= maximum:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "original_bytes": len(encoded.encode()),
            "preview": encoded[: max(maximum - 200, 0)],
        },
        separators=(",", ":"),
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
