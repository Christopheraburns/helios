"""Metadata catalog plugins (``helios.catalogs``) and the optional capabilities they offer.

Tables and columns always come from the engine (``Engine.tables`` / ``columns``:
information_schema, the SQLAlchemy inspector, or the engine's own statements), so a
deployment needs no catalog plugin to harvest. A catalog adds what the database does
not know, each part optional:

* glossary: business terms and the columns assigned to them (GlossaryCatalog)
* lineage: what a column is derived from and feeds (LineageCatalog)

A catalog plugin is a class whose ``from_catalog(CatalogSettings)`` builds it;
``catalog_for`` loads the plugin a ``[catalogs.<name>]`` entry names. helios-cloudera's
Atlas client provides both capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..settings import CatalogSettings

GLOSSARY = "glossary"
LINEAGE = "lineage"


class CatalogError(RuntimeError):
    """A catalog request failed. Catalog plugins raise subclasses (AtlasError)."""


@runtime_checkable
class GlossaryCatalog(Protocol):
    def list_glossaries(self) -> list[dict[str, Any]]:
        """Each glossary as a dict with at least ``guid`` and ``name``."""
        ...

    def list_terms(self, glossary_guid: str) -> list[dict[str, Any]]:
        """Each term with ``guid``, ``name`` and optionally ``shortDescription``,
        ``longDescription`` and ``abbreviation``."""
        ...

    def assigned_entities(self, term_guid: str) -> list[dict[str, Any]]:
        """The entities (columns) assigned to the term, each with ``displayText``."""
        ...


@dataclass(frozen=True)
class ColumnLineage:
    """A column's lineage graph: entities by id (each with ``type`` and ``name``) and
    the edges between them, upstream and downstream."""

    column: str
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"column": self.column, "entities": self.entities,
                "edges": [{"from": a, "to": b} for a, b in self.edges]}


@runtime_checkable
class LineageCatalog(Protocol):
    def column_lineage(self, database: str, table: str, column: str, depth: int = 3) -> ColumnLineage | None:
        """The column's lineage ``depth`` hops each way; None when the catalog does not know the column."""
        ...


def capabilities(catalog: object | None) -> frozenset[str]:
    """What ``catalog`` offers: ``glossary``, ``lineage``, both or neither."""
    found = set()
    if isinstance(catalog, GlossaryCatalog):
        found.add(GLOSSARY)
    if isinstance(catalog, LineageCatalog):
        found.add(LINEAGE)
    return frozenset(found)


def catalog_for(catalog: CatalogSettings | None) -> object | None:
    """The catalog a ``[catalogs.<name>]`` entry (``Settings.catalog(name)``) names, built
    by its plugin; None for no entry. Raises PluginNotFound when the plugin is not installed."""
    if catalog is None:
        return None
    from .. import plugins

    plugin = plugins.load(plugins.CATALOGS, catalog.catalog)
    if not callable(getattr(plugin, "from_catalog", None)):
        raise plugins.PluginError(f"catalog plugin {catalog.catalog!r} has no from_catalog: {plugin!r}")
    built = plugin.from_catalog(catalog)
    if not capabilities(built):
        raise plugins.PluginError(f"catalog plugin {catalog.catalog!r} offers neither a glossary nor lineage")
    return built
