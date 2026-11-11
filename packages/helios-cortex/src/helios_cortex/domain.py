"""Registry records: data sources and the models built on them.

These classes describe resource identity only. They prescribe no database, API
representation or connection-secret store, and no ownership: who may see a model
is the host's decision (``helios_core.visibility``).

A :class:`DataSource` is the reusable physical connection and owns references to
its harvested/profiled snapshots. A :class:`Model` is a semantic interpretation
that refers to one or more data sources; it never owns or duplicates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


def _require(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_unique(values: tuple[str, ...], field_name: str) -> None:
    if any(not value or not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain empty values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


@dataclass(frozen=True)
class DataSource:
    """A physical lakehouse or warehouse connection reusable by many models.

    ``connection_ref`` names a connection in Settings (``Settings.resolve_connection``).
    Harvest/profile artifacts belong here so creating another model does not imply
    collecting the same physical metadata again.
    """

    id: str
    name: str
    connector: str
    connection_ref: str
    snapshot_ids: tuple[str, ...] = ()
    # Crawling (DS-2): what to crawl (validated per connector type by
    # helios_core._pending.crawler.sources) and how. Empty for warehouse sources.
    description: str = ""
    scope: dict = field(default_factory=dict, hash=False)
    crawl: dict = field(default_factory=dict, hash=False)
    updated_at: str | None = None
    updated_by: str | None = None

    def __post_init__(self) -> None:
        _require(self.id, "data source id")
        _require(self.name, "data source name")
        _require(self.connector, "data source connector")
        _require(self.connection_ref, "data source connection_ref")
        _require_unique(self.snapshot_ids, "snapshot_ids")


@dataclass(frozen=True)
class DataSourceReference:
    """A model's selection from a separately managed data source.

    ``selected_assets`` contains implementation-neutral qualified asset names.
    An empty tuple means the model has not narrowed the assets available through
    the data source.
    """

    data_source_id: str
    selected_assets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.data_source_id, "data source reference id")
        _require_unique(self.selected_assets, "selected_assets")


@dataclass(frozen=True)
class Model:
    """A semantic interpretation of selected assets from one or more data sources."""

    id: str
    name: str
    data_sources: tuple[DataSourceReference, ...]
    description: str = ""
    version_ids: tuple[str, ...] = ()
    discovery_run_ids: tuple[str, ...] = ()
    glossary_id: str | None = None
    semantic_model_id: str | None = None
    ontology_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.id, "model id")
        _require(self.name, "model name")
        if not self.data_sources:
            raise ValueError("model must reference at least one data source")
        source_ids = tuple(reference.data_source_id for reference in self.data_sources)
        _require_unique(source_ids, "model data source references")
        _require_unique(self.version_ids, "version_ids")
        _require_unique(self.discovery_run_ids, "discovery_run_ids")
        for field_name in ("glossary_id", "semantic_model_id", "ontology_id"):
            value = getattr(self, field_name)
            if value is not None:
                _require(value, field_name)

    def resolve_data_sources(
        self,
        data_sources: Mapping[str, DataSource],
    ) -> tuple[DataSource, ...]:
        """The data sources this model references, from ``data_sources`` by id."""
        resolved = []
        for reference in self.data_sources:
            try:
                resolved.append(data_sources[reference.data_source_id])
            except KeyError as exc:
                raise ValueError(
                    f"model {self.id!r} references unknown data source {reference.data_source_id!r}"
                ) from exc
        return tuple(resolved)
