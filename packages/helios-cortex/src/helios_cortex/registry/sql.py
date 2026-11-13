"""SqlRegistry: the model registry in any database SQLAlchemy supports, chosen by URL.

    SqlRegistry("sqlite:////home/me/.local/share/helios/state/registry.db")
    SqlRegistry("postgresql+psycopg://helios@db.example.com/helios")

The schema is managed by Alembic migrations shipped in this package (version table
``helios_registry_version``, tables prefixed ``helios_``, so the registry can share a
database with an application). ``upgrade()`` brings a database to the current schema;
until then every call raises RegistryNotReady. ``from_settings`` upgrades the local
SQLite default by itself and leaves shared databases to the operator
(``python -m helios_cortex.registry upgrade``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from helios_cortex._optional import missing_extra
from helios_cortex.domain import DataSource, DataSourceReference, Model

from . import DRAFT, RegisteredModel, RegistryError, RevisionConflict, SemanticRevision

if TYPE_CHECKING:
    from helios_cortex.settings import Settings

MIGRATIONS = Path(__file__).with_name("migrations")
VERSION_TABLE = "helios_registry_version"

metadata = sa.MetaData()

data_sources = sa.Table(
    "helios_data_sources", metadata,
    sa.Column("id", sa.String(255), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("connector", sa.String(255), nullable=False),
    sa.Column("connection_ref", sa.String(255), nullable=False),
    sa.Column("description", sa.Text, nullable=False, default=""),
    sa.Column("snapshot_ids", sa.JSON, nullable=False),
    sa.Column("scope", sa.JSON, nullable=False),
    sa.Column("crawl", sa.JSON, nullable=False),
    sa.Column("updated_at", sa.String(64)),
    sa.Column("updated_by", sa.String(255)),
)

models = sa.Table(
    "helios_models", metadata,
    sa.Column("id", sa.String(255), primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("description", sa.Text, nullable=False, default=""),
    sa.Column("status", sa.String(64), nullable=False),
    sa.Column("created_by", sa.String(255)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("glossary_id", sa.String(255)),
    sa.Column("semantic_model_id", sa.String(255)),
    sa.Column("ontology_id", sa.String(255)),
    sa.Column("version_ids", sa.JSON, nullable=False),
    sa.Column("discovery_run_ids", sa.JSON, nullable=False),
)

model_data_sources = sa.Table(
    "helios_model_data_sources", metadata,
    sa.Column("model_id", sa.String(255), sa.ForeignKey("helios_models.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("data_source_id", sa.String(255), sa.ForeignKey("helios_data_sources.id", ondelete="RESTRICT"),
              primary_key=True),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("selected_assets", sa.JSON, nullable=False),
)

semantic_revisions = sa.Table(
    "helios_semantic_revisions", metadata,
    sa.Column("id", sa.String(255), primary_key=True),
    sa.Column("model_id", sa.String(255), sa.ForeignKey("helios_models.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("artifact_path", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("ossie_version", sa.String(64)),
    sa.Column("discovery_run_id", sa.String(255)),
    sa.Column("published_by", sa.String(255)),
    sa.Index("ix_helios_semantic_revisions_model_published", "model_id", "published_at"),
)


class RegistryNotReady(RegistryError):
    """The database's registry schema is missing or older than this helios-cortex."""


def _utc(value: datetime) -> datetime:
    # SQLite keeps no time zone; everything is stored in UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class SqlRegistry:
    def __init__(self, url: str, *, engine: sa.Engine | None = None):
        try:
            # pre-ping: a connection dropped by a failover or an idle timeout is replaced
            # before use instead of failing the next call.
            self._engine = engine or sa.create_engine(url, pool_pre_ping=True)
        except ModuleNotFoundError as exc:  # the database's driver is not installed
            if sa.make_url(url).get_backend_name() == "postgresql":
                raise missing_extra("a Postgres registry", "postgres", exc) from exc
            raise ModuleNotFoundError(
                f"the registry's database driver is not installed: {exc.name}", name=exc.name) from exc
        if self._engine.dialect.name == "sqlite":
            sa.event.listen(self._engine, "connect", _enable_sqlite_foreign_keys)
        self._ready = False

    def __repr__(self) -> str:
        return f"SqlRegistry({self._engine.url.render_as_string(hide_password=True)!r})"

    @classmethod
    def from_settings(cls, settings: Settings, *, upgrade: bool | None = None) -> SqlRegistry:
        """The registry at ``settings.registry_url``. ``upgrade`` defaults to True for
        SQLite (the local default, one user) and False for a shared database."""
        assert settings.registry_url is not None  # Settings fills it in from data_dir
        url = sa.make_url(settings.registry_url.get_secret_value())
        if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        registry = cls(url.render_as_string(hide_password=False))
        if upgrade if upgrade is not None else url.get_backend_name() == "sqlite":
            registry.upgrade()
        return registry

    # ------------------------------------------------------------ schema
    def _alembic_config(self, connection: sa.Connection):
        from alembic.config import Config

        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS))
        config.attributes["connection"] = connection
        return config

    def upgrade(self, revision: str = "head") -> None:
        """Bring the database to the current registry schema (Alembic)."""
        from alembic import command

        with self._engine.begin() as connection:
            command.upgrade(self._alembic_config(connection), revision)
        self._ready = False

    def current_revision(self) -> str | None:
        """The schema revision the database is at; None before the first upgrade."""
        from alembic.runtime.migration import MigrationContext

        with self._engine.connect() as connection:
            return MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE}).get_current_revision()

    def head_revision(self) -> str:
        from alembic.script import ScriptDirectory

        head = ScriptDirectory(str(MIGRATIONS)).get_current_head()
        assert head is not None  # the package ships its migrations
        return head

    def _check_ready(self) -> None:
        if self._ready:
            return
        current, head = self.current_revision(), self.head_revision()
        if current != head:
            raise RegistryNotReady(
                f"the registry schema is at {current or 'nothing'}, not {head}; "
                "run `python -m helios_cortex.registry upgrade` (or SqlRegistry.upgrade())")
        self._ready = True

    def _begin(self):
        self._check_ready()
        return self._engine.begin()

    # ------------------------------------------------------------ data sources
    @staticmethod
    def _data_source_row(d: DataSource) -> dict[str, Any]:
        return {"id": d.id, "name": d.name, "connector": d.connector, "connection_ref": d.connection_ref,
                "description": d.description, "snapshot_ids": list(d.snapshot_ids), "scope": d.scope,
                "crawl": d.crawl, "updated_at": d.updated_at, "updated_by": d.updated_by}

    @staticmethod
    def _data_source(row: Any) -> DataSource:
        return DataSource(row.id, row.name, row.connector, row.connection_ref, tuple(row.snapshot_ids),
                          row.description, dict(row.scope), dict(row.crawl), row.updated_at, row.updated_by)

    def save_data_source(self, data_source: DataSource) -> DataSource:
        row = self._data_source_row(data_source)
        with self._begin() as c:
            if c.execute(sa.select(data_sources.c.id).where(data_sources.c.id == data_source.id)).first():
                c.execute(data_sources.update().where(data_sources.c.id == data_source.id).values(**row))
            else:
                c.execute(data_sources.insert().values(**row))
        saved = self.data_source(data_source.id)
        assert saved is not None  # just written
        return saved

    def data_source(self, data_source_id: str) -> DataSource | None:
        with self._begin() as c:
            row = c.execute(sa.select(data_sources).where(data_sources.c.id == data_source_id)).first()
        return self._data_source(row) if row else None

    def data_sources(self) -> list[DataSource]:
        with self._begin() as c:
            rows = c.execute(sa.select(data_sources).order_by(sa.func.lower(data_sources.c.name), data_sources.c.id))
            return [self._data_source(r) for r in rows]

    def delete_data_source(self, data_source_id: str) -> bool:
        with self._begin() as c:
            users = sorted(r.model_id for r in c.execute(
                sa.select(model_data_sources.c.model_id).where(model_data_sources.c.data_source_id == data_source_id)))
            if users:
                raise RegistryError(f"data source {data_source_id!r} is used by model(s) {', '.join(users)}")
            return c.execute(data_sources.delete().where(data_sources.c.id == data_source_id)).rowcount > 0

    # ------------------------------------------------------------ models
    def save_model(self, model: Model, *, status: str = DRAFT, created_by: str | None = None) -> RegisteredModel:
        if not status or not status.strip():
            raise RegistryError("model status must not be empty")
        now = datetime.now(timezone.utc)
        fields = {"name": model.name, "description": model.description, "status": status, "updated_at": now,
                  "glossary_id": model.glossary_id, "semantic_model_id": model.semantic_model_id,
                  "ontology_id": model.ontology_id, "version_ids": list(model.version_ids),
                  "discovery_run_ids": list(model.discovery_run_ids)}
        with self._begin() as c:
            ids = [r.data_source_id for r in model.data_sources]
            known = {r.id for r in c.execute(sa.select(data_sources.c.id).where(data_sources.c.id.in_(ids)))}
            missing = [i for i in ids if i not in known]
            if missing:
                raise RegistryError(f"model {model.id!r} references unknown data source(s) {', '.join(missing)}")
            if c.execute(sa.select(models.c.id).where(models.c.id == model.id)).first():
                c.execute(models.update().where(models.c.id == model.id).values(**fields))
            else:
                c.execute(models.insert().values(id=model.id, created_by=created_by, created_at=now, **fields))
            c.execute(model_data_sources.delete().where(model_data_sources.c.model_id == model.id))
            c.execute(model_data_sources.insert(), [
                {"model_id": model.id, "data_source_id": r.data_source_id, "position": i,
                 "selected_assets": list(r.selected_assets)}
                for i, r in enumerate(model.data_sources)])
        saved = self.registered_model(model.id)
        assert saved is not None  # just written
        return saved

    def _registered(self, c: sa.Connection, rows: list[Any]) -> list[RegisteredModel]:
        if not rows:
            return []
        refs: dict[str, list[DataSourceReference]] = {r.id: [] for r in rows}
        for ref in c.execute(sa.select(model_data_sources)
                             .where(model_data_sources.c.model_id.in_(list(refs)))
                             .order_by(model_data_sources.c.model_id, model_data_sources.c.position)):
            refs[ref.model_id].append(DataSourceReference(ref.data_source_id, tuple(ref.selected_assets)))
        return [RegisteredModel(
            model=Model(r.id, r.name, tuple(refs[r.id]), r.description, tuple(r.version_ids),
                        tuple(r.discovery_run_ids), r.glossary_id, r.semantic_model_id, r.ontology_id),
            status=r.status, created_at=_utc(r.created_at), updated_at=_utc(r.updated_at), created_by=r.created_by,
        ) for r in rows]

    def registered_model(self, model_id: str) -> RegisteredModel | None:
        with self._begin() as c:
            found = self._registered(c, list(c.execute(sa.select(models).where(models.c.id == model_id))))
        return found[0] if found else None

    def model(self, model_id: str) -> Model | None:
        registered = self.registered_model(model_id)
        return registered.model if registered else None

    def models(self, *, status: str | None = None) -> list[RegisteredModel]:
        query = sa.select(models).order_by(sa.func.lower(models.c.name), models.c.id)
        if status is not None:
            query = query.where(models.c.status == status)
        with self._begin() as c:
            return self._registered(c, list(c.execute(query)))

    # ------------------------------------------------------------ semantic revisions
    @staticmethod
    def _revision(row: Any) -> SemanticRevision:
        return SemanticRevision(row.id, row.model_id, row.sha256, row.artifact_path, _utc(row.published_at),
                                row.size_bytes, row.ossie_version, row.discovery_run_id, row.published_by)

    def save_semantic_revision(self, revision: SemanticRevision) -> SemanticRevision:
        values = {"id": revision.id, "model_id": revision.model_id, "sha256": revision.sha256,
                  "artifact_path": revision.artifact_path, "published_at": _utc(revision.published_at),
                  "size_bytes": revision.size_bytes, "ossie_version": revision.ossie_version,
                  "discovery_run_id": revision.discovery_run_id, "published_by": revision.published_by}
        try:
            with self._begin() as c:
                if not c.execute(sa.select(models.c.id).where(models.c.id == revision.model_id)).first():
                    raise RegistryError(f"revision {revision.id!r} is for unknown model {revision.model_id!r}")
                if not c.execute(sa.select(semantic_revisions.c.id)
                                 .where(semantic_revisions.c.id == revision.id)).first():
                    c.execute(semantic_revisions.insert().values(**values))
        except IntegrityError:
            pass  # another writer saved this id first; compare below
        stored = self.semantic_revision(revision.id)
        if stored is None:
            raise RegistryError(f"semantic revision {revision.id!r} was not saved")
        if not stored.same_content(revision):
            raise RevisionConflict(f"semantic revision {revision.id!r} is immutable")
        return stored

    def semantic_revision(self, revision_id: str) -> SemanticRevision | None:
        with self._begin() as c:
            row = c.execute(sa.select(semantic_revisions).where(semantic_revisions.c.id == revision_id)).first()
        return self._revision(row) if row else None

    def semantic_revisions(self, model_id: str) -> list[SemanticRevision]:
        with self._begin() as c:
            rows = c.execute(sa.select(semantic_revisions).where(semantic_revisions.c.model_id == model_id)
                             .order_by(semantic_revisions.c.published_at.desc(), semantic_revisions.c.id.desc()))
            return [self._revision(r) for r in rows]

    def latest_semantic_revision(self, model_id: str) -> SemanticRevision | None:
        revisions = self.semantic_revisions(model_id)
        return revisions[0] if revisions else None
