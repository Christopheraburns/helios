"""What the harvester needs from a metadata catalog plugin (``helios.catalogs``).

A catalog with a business glossary implements GlossaryCatalog; helios-cloudera's
Atlas client is one. 3.7 adds the default catalog plugin and makes the glossary
and lineage optional capabilities.
"""
from __future__ import annotations

from typing import Any, Protocol


class CatalogError(RuntimeError):
    """A catalog request failed. Catalog plugins raise subclasses (AtlasError)."""


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
