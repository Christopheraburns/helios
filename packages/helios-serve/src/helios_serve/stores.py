"""What the tools read: published models, the registry, connections and catalogs.

``Stores.from_settings(settings)`` is a host's usual start. Every part can be replaced,
which is how a host adds its own rules: ``visibility`` decides which models a caller may
see, ``data_policy`` which queries may run, ``audit`` where tool records go, and
``engines`` / ``catalogs`` how a connection or a catalog is built.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from helios_cortex.audit_sink import AuditSink
from helios_cortex.data_authorization import DataPolicy
from helios_cortex.ossie import SemanticModel
from helios_cortex.visibility import AllModels, ModelVisibility

if TYPE_CHECKING:
    from helios_cortex.artifacts import ArtifactStore
    from helios_cortex.engines import Engine
    from helios_cortex.settings import Settings

PUBLISHED = "published"
PINNED = "published revision"


class ModelNotAvailable(LookupError):
    """No published model of that name (or no published model at all)."""


class Models(Protocol):
    """Where the tools get semantic models from (``ModelStore``, or a host's own)."""

    def sources(self) -> list[dict[str, Any]]:
        """``{"name", "status", ...}`` for every model there is to answer from."""
        ...

    def load(self, name: str | None = None) -> tuple[dict[str, Any], SemanticModel]:
        """The source and the model; the first source when ``name`` is None."""
        ...


class Registry(Protocol):
    """The part of the model registry the tools read (``ModelRegistry`` has it)."""

    def model(self, model_id: str) -> Any: ...

    def data_source(self, data_source_id: str) -> Any: ...


class ModelStore:
    """The published models in an ``ArtifactStore``: each model's active revision, or a
    pinned one. Revisions are immutable and named by their hash, so each is read and
    parsed once; a new activation is picked up on the next load."""

    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts
        self._cache: dict[tuple[str, str], SemanticModel] = {}
        self._lock = threading.Lock()

    def sources(self) -> list[dict[str, Any]]:
        out = []
        for model_id in self.artifacts.published_model_ids():
            active = self.artifacts.active_revision(model_id)
            out.append({"name": model_id, "status": PUBLISHED,
                        "revision_id": active.revision_id if active else None})
        return out

    def load(self, name: str | None = None, revision: str | None = None) -> tuple[dict[str, Any], SemanticModel]:
        """The model's active revision, or ``revision`` (verified against its hash)."""
        sources = self.sources()
        if not sources:
            raise ModelNotAvailable("no model is published")
        source = next((s for s in sources if s["name"] == name), None) if name else sources[0]
        if source is None:
            raise ModelNotAvailable(f"model {name!r} is not published")
        model_id = source["name"]
        if revision is not None:
            text = self.artifacts.read_revision(model_id, revision, "json")
            source = {"name": model_id, "status": PINNED, "revision_id": revision}
        elif source["revision_id"] is not None:
            revision = source["revision_id"]
            text = None
        else:                                         # published before revisions existed
            text = self.artifacts.read_published_ossie(model_id, "json")
            revision = "legacy:" + hashlib.sha256(text.encode()).hexdigest()
        key = (model_id, revision)
        with self._lock:
            cached = self._cache.get(key)
        if cached is None:
            if text is None:
                text = self.artifacts.read_revision(model_id, revision, "json")
            cached = SemanticModel(json.loads(text))
            with self._lock:
                self._cache[key] = cached
        return source, cached


def _default_engine(settings: Settings) -> Callable[[str | None], Engine | None]:
    def build(connection: str | None) -> Engine | None:
        entry = settings.connection(connection)
        if entry is None:
            return None
        from helios_cortex.engines import engine_for

        return engine_for(entry)

    return build


def _default_catalog(settings: Settings) -> Callable[[], object | None]:
    def build() -> object | None:
        from helios_cortex.catalog import catalog_for

        return catalog_for(settings.catalog())

    return build


@dataclass
class Stores:
    """Everything a tool reads. ``engines(connection_name)`` builds the engine for a
    connection (None when there is none); ``catalogs()`` the catalog for lineage."""

    settings: Settings
    registry: Registry
    models: Models
    visibility: ModelVisibility = field(default_factory=AllModels)
    data_policy: DataPolicy = field(default_factory=DataPolicy)
    audit: AuditSink | None = None
    engines: Callable[[str | None], Engine | None] | None = None
    catalogs: Callable[[], object | None] | None = None

    def __post_init__(self) -> None:
        if self.engines is None:
            self.engines = _default_engine(self.settings)
        if self.catalogs is None:
            self.catalogs = _default_catalog(self.settings)

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: Any) -> Stores:
        """The registry at ``settings.registry_url`` and the models published to the
        settings' artifact store; ``overrides`` replace any part."""
        if "registry" not in overrides:
            from helios_cortex.registry import SqlRegistry

            overrides["registry"] = SqlRegistry.from_settings(settings)
        if "models" not in overrides:
            from helios_cortex.artifacts import ArtifactStore

            overrides["models"] = ModelStore(ArtifactStore.from_settings(settings))
        return cls(settings=settings, **overrides)


def model_connection(settings: Settings, registry: Registry, model: Any) -> tuple[str | None, str | None]:
    """The connection a model's data sources resolve to, or an error message when they
    resolve to more than one: a query runs on one connection. A model with no data
    sources (or none the registry knows) uses the default connection."""
    refs = [
        source.connection_ref
        for reference in model.data_sources
        if (source := registry.data_source(reference.data_source_id)) is not None
    ]
    names = {settings.resolve_connection(ref) for ref in refs} if refs else {settings.resolve_connection()}
    names.discard(None)
    if len(names) > 1:
        return None, ("the model's data sources use different connections ("
                      + ", ".join(sorted(names)) + "); a query runs on one connection")
    return next(iter(names), None), None
