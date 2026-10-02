"""Connectors: one per kind of location (DS-1). ``build`` makes the connector a
data source's ``connector`` names, with credentials resolved from its
``connection_ref`` (never stored in Helios)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from helios_core.crawler.sources import validate_scope

from .base import ConnectionTest, Connector, Fetched, SourceAsset, plan_incremental
from .helios_ds import HeliosDsConnector, object_reader
from .object_store import ObjectStoreConnector
from .table_rows import TableRowsConnector

__all__ = [
    "ConnectionTest",
    "Connector",
    "Fetched",
    "HeliosDsConnector",
    "ObjectStoreConnector",
    "SourceAsset",
    "TableRowsConnector",
    "build",
    "plan_incremental",
]


def build(
    source_id: str,
    connector: str,
    connection_ref: str,
    scope: dict[str, Any],
    *,
    cursor: Callable[[], Any],
    s3_client_for: Callable[[str], Any],
) -> Connector:
    """The connector for a data source. ``cursor`` opens an Impala cursor as the
    crawler identity; ``s3_client_for(name)`` returns the S3 client behind a
    Workbench data connection."""
    scope = validate_scope(connector, scope)
    if connector == "helios_ds":
        return HeliosDsConnector(
            source_id, scope, cursor, object_reader(s3_client_for(connection_ref))
        )
    if connector == "object_store":
        return ObjectStoreConnector(source_id, scope, s3_client_for(connection_ref))
    if connector == "table_rows":
        if connection_ref != "impala":
            raise ValueError(
                "table_rows sources read through Helios's Impala settings: connection_ref must be 'impala'"
            )
        return TableRowsConnector(source_id, scope, cursor)
    raise ValueError(f"no connector for {connector!r}")
