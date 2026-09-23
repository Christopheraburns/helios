"""Core resource model for the multi-organization Helios platform.

These classes describe resource identity and ownership only.  They deliberately do
not prescribe a database, API representation, connection-secret store, or detailed
authorization model.

A :class:`DataSource` is the reusable physical connection and owns references to
its harvested/profiled snapshots.  A :class:`Model` is a semantic interpretation
that refers to one or more data sources; it never owns or duplicates them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


def _require(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_unique(values: tuple[str, ...], field_name: str) -> None:
    if any(not value or not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain empty values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


@dataclass(frozen=True)
class Organization:
    """Top-level ownership boundary for Helios resources."""

    id: str
    name: str
    member_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.id, "organization id")
        _require(self.name, "organization name")
        _require_unique(self.member_ids, "member_ids")

    def validate_resources(
        self,
        data_sources: Iterable[DataSource],
        models: Iterable[Model],
    ) -> None:
        """Validate ownership and model-to-data-source references for this organization."""
        sources = {source.id: source for source in data_sources}
        for source in sources.values():
            if source.organization_id != self.id:
                raise ValueError(
                    f"data source {source.id!r} belongs to organization "
                    f"{source.organization_id!r}, not {self.id!r}"
                )

        for model in models:
            if model.organization_id != self.id:
                raise ValueError(
                    f"model {model.id!r} belongs to organization "
                    f"{model.organization_id!r}, not {self.id!r}"
                )
            for reference in model.data_sources:
                source = sources.get(reference.data_source_id)
                if source is None:
                    raise ValueError(
                        f"model {model.id!r} references unknown data source "
                        f"{reference.data_source_id!r}"
                    )


@dataclass(frozen=True)
class DataSource:
    """A physical lakehouse or warehouse connection reusable by many models.

    ``connection_ref`` is an opaque reference to configuration and secrets managed
    outside this domain object.  Harvest/profile artifacts belong here so creating
    another model does not imply collecting the same physical metadata again.
    """

    id: str
    organization_id: str
    name: str
    connector: str
    connection_ref: str
    snapshot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.id, "data source id")
        _require(self.organization_id, "data source organization_id")
        _require(self.name, "data source name")
        _require(self.connector, "data source connector")
        _require(self.connection_ref, "data source connection_ref")
        _require_unique(self.snapshot_ids, "snapshot_ids")


@dataclass(frozen=True)
class DataSourceReference:
    """A model's selection from a separately managed data source.

    ``selected_assets`` contains implementation-neutral qualified asset names.
    An empty tuple means the model has not narrowed the assets available through
    the data source.  Detailed authorization remains outside this initial model.
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
    organization_id: str
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
        _require(self.organization_id, "model organization_id")
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
        """Resolve references while enforcing the model's organization boundary."""
        resolved = []
        for reference in self.data_sources:
            try:
                source = data_sources[reference.data_source_id]
            except KeyError as exc:
                raise ValueError(
                    f"model {self.id!r} references unknown data source "
                    f"{reference.data_source_id!r}"
                ) from exc
            if source.organization_id != self.organization_id:
                raise ValueError(
                    f"model {self.id!r} cannot reference data source {source.id!r} "
                    "from another organization"
                )
            resolved.append(source)
        return tuple(resolved)
