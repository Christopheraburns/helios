"""The model registry: the models a deployment serves, the data sources they read, and
the semantic revisions published for each.

Libraries depend on the ModelRegistry protocol, not on a database. InMemoryRegistry
is the reference implementation (and a test double); SqlRegistry stores the same
records in any database SQLAlchemy supports, chosen by URL (``Settings.registry_url``). The registry records no ownership: which models a
caller may see is the host's ModelVisibility.

Rules every backend keeps:

* a model may reference only data sources already in the registry;
* saving a model again updates it but keeps who created it and when;
* a data source a model uses cannot be deleted;
* a semantic revision is immutable: saving the same one again returns it unchanged,
  saving different metadata under its id raises RevisionConflict.
"""
from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol

from helios_core.domain import DataSource, Model

DRAFT = "draft"
PUBLISHED = "published"


class RegistryError(ValueError):
    """A registry rule was broken: an unknown reference, a data source in use, an empty status."""


class RevisionConflict(RegistryError):
    """A semantic revision id was saved again with different metadata."""


@dataclass(frozen=True)
class RegisteredModel:
    """A model as the registry holds it: its status and when, and by whom, it was created."""

    model: Model
    status: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None   # the caller's username, when the host passed one

    @property
    def id(self) -> str:
        return self.model.id


@dataclass(frozen=True)
class SemanticRevision:
    """One published semantic model file: immutable, identified by id and content hash."""

    id: str
    model_id: str
    sha256: str
    artifact_path: str
    published_at: datetime
    size_bytes: int
    ossie_version: str | None = None
    discovery_run_id: str | None = None
    published_by: str | None = None

    def same_content(self, other: SemanticRevision) -> bool:
        """True when ``other`` describes the same file (who and when may differ on a retry)."""
        return (self.model_id, self.sha256, self.artifact_path, self.ossie_version, self.size_bytes) == (
            other.model_id, other.sha256, other.artifact_path, other.ossie_version, other.size_bytes)


class ModelRegistry(Protocol):
    # data sources
    def save_data_source(self, data_source: DataSource) -> DataSource: ...

    def data_source(self, data_source_id: str) -> DataSource | None: ...

    def data_sources(self) -> list[DataSource]:
        """Every data source, by name then id."""
        ...

    def delete_data_source(self, data_source_id: str) -> bool:
        """False when there was none; RegistryError when a model still uses it."""
        ...

    # models
    def save_model(self, model: Model, *, status: str = DRAFT, created_by: str | None = None) -> RegisteredModel: ...

    def registered_model(self, model_id: str) -> RegisteredModel | None: ...

    def model(self, model_id: str) -> Model | None: ...

    def models(self, *, status: str | None = None) -> list[RegisteredModel]:
        """Every model (or those with ``status``), by name then id."""
        ...

    # semantic revisions
    def save_semantic_revision(self, revision: SemanticRevision) -> SemanticRevision: ...

    def semantic_revision(self, revision_id: str) -> SemanticRevision | None: ...

    def semantic_revisions(self, model_id: str) -> list[SemanticRevision]:
        """The model's revisions, newest first."""
        ...

    def latest_semantic_revision(self, model_id: str) -> SemanticRevision | None: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryRegistry:
    """The reference ModelRegistry, held in memory. Thread-safe; records are copied in
    and out, so callers cannot change what the registry holds."""

    def __init__(self, *, clock: Callable[[], datetime] = _utcnow):
        self._clock = clock
        self._lock = threading.Lock()
        self._data_sources: dict[str, DataSource] = {}
        self._models: dict[str, RegisteredModel] = {}
        self._revisions: dict[str, SemanticRevision] = {}

    # ------------------------------------------------------------ data sources
    def save_data_source(self, data_source: DataSource) -> DataSource:
        with self._lock:
            self._data_sources[data_source.id] = copy.deepcopy(data_source)
            return copy.deepcopy(data_source)

    def data_source(self, data_source_id: str) -> DataSource | None:
        with self._lock:
            return copy.deepcopy(self._data_sources.get(data_source_id))

    def data_sources(self) -> list[DataSource]:
        with self._lock:
            return [copy.deepcopy(d) for d in sorted(self._data_sources.values(), key=lambda d: (d.name.lower(), d.id))]

    def delete_data_source(self, data_source_id: str) -> bool:
        with self._lock:
            if data_source_id not in self._data_sources:
                return False
            users = sorted(m.id for m in self._models.values()
                           if any(r.data_source_id == data_source_id for r in m.model.data_sources))
            if users:
                raise RegistryError(f"data source {data_source_id!r} is used by model(s) {', '.join(users)}")
            del self._data_sources[data_source_id]
            return True

    # ------------------------------------------------------------ models
    def save_model(self, model: Model, *, status: str = DRAFT, created_by: str | None = None) -> RegisteredModel:
        if not status or not status.strip():
            raise RegistryError("model status must not be empty")
        with self._lock:
            missing = [r.data_source_id for r in model.data_sources if r.data_source_id not in self._data_sources]
            if missing:
                raise RegistryError(f"model {model.id!r} references unknown data source(s) {', '.join(missing)}")
            now = self._clock()
            previous = self._models.get(model.id)
            stored = RegisteredModel(
                model=copy.deepcopy(model),
                status=status,
                created_at=previous.created_at if previous else now,
                updated_at=now,
                created_by=previous.created_by if previous else created_by,
            )
            self._models[model.id] = stored
            return copy.deepcopy(stored)

    def registered_model(self, model_id: str) -> RegisteredModel | None:
        with self._lock:
            return copy.deepcopy(self._models.get(model_id))

    def model(self, model_id: str) -> Model | None:
        registered = self.registered_model(model_id)
        return registered.model if registered else None

    def models(self, *, status: str | None = None) -> list[RegisteredModel]:
        with self._lock:
            chosen = [m for m in self._models.values() if status is None or m.status == status]
            return [copy.deepcopy(m) for m in sorted(chosen, key=lambda m: (m.model.name.lower(), m.id))]

    # ------------------------------------------------------------ semantic revisions
    def save_semantic_revision(self, revision: SemanticRevision) -> SemanticRevision:
        with self._lock:
            if revision.model_id not in self._models:
                raise RegistryError(f"revision {revision.id!r} is for unknown model {revision.model_id!r}")
            stored = self._revisions.get(revision.id)
            if stored is None:
                self._revisions[revision.id] = replace(revision)
                return replace(revision)
            if not stored.same_content(revision):
                raise RevisionConflict(f"semantic revision {revision.id!r} is immutable")
            return replace(stored)

    def semantic_revision(self, revision_id: str) -> SemanticRevision | None:
        with self._lock:
            stored = self._revisions.get(revision_id)
            return replace(stored) if stored else None

    def semantic_revisions(self, model_id: str) -> list[SemanticRevision]:
        with self._lock:
            chosen = [r for r in self._revisions.values() if r.model_id == model_id]
            return sorted(chosen, key=lambda r: (r.published_at, r.id), reverse=True)

    def latest_semantic_revision(self, model_id: str) -> SemanticRevision | None:
        revisions = self.semantic_revisions(model_id)
        return revisions[0] if revisions else None


from helios_core.registry.sql import RegistryNotReady as RegistryNotReady  # noqa: E402 - needs the records above
from helios_core.registry.sql import SqlRegistry as SqlRegistry  # noqa: E402
