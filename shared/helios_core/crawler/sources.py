"""Crawlable data sources: connector types and their configuration (DS-1, DS-2).

A data source (helios_core.domain.DataSource, stored in Helios's metadata store)
says *where* documents live. For crawling it carries:

- ``connector``: one of ``CONNECTOR_TYPES``;
- ``connection_ref``: a *reference* to credentials, never the credentials: a
  Workbench data connection name, or ``impala`` for Helios's Impala settings;
- ``scope``: what to crawl there, validated by the connector type's model below;
- ``crawl``: how to crawl it (``CrawlConfig``).

Warehouse data sources used by semantic models (other connector values) are not
crawled.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeliosDsScope(_Model):
    """A READY Helios-DS dataset, read through helios_ds.crawlable_artifacts."""

    dataset_id: str = Field(min_length=1, description="The Helios-DS dataset to crawl")


class ObjectStoreScope(_Model):
    """Objects in an S3-compatible store (S3, Ozone) under a prefix."""

    bucket: str = Field(min_length=1)
    prefix: str = Field("", description="Only keys under this prefix")
    include: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Glob patterns on the key relative to the prefix; at least one must match",
    )
    exclude: list[str] = Field(default_factory=list, description="Glob patterns to skip")
    max_bytes: int = Field(50_000_000, ge=1, description="Larger objects are skipped")
    max_objects: int = Field(100_000, ge=1, description="Stop listing after this many")


class TableRowsScope(_Model):
    """Rows of a lakehouse table as documents: each row one asset, each text column
    one segment. Identifiers only: no free SQL."""

    table: str = Field(description="database.table")
    key_columns: list[str] = Field(min_length=1, description="Columns that identify a row")
    text_columns: list[str] = Field(min_length=1, description="Columns holding the text")
    timestamp_column: str | None = Field(None, description="The row's document date, if any")
    filters: dict[str, str | int | float | bool] = Field(
        default_factory=dict, description="Equality filters, column -> value"
    )
    max_rows: int = Field(100_000, ge=1)

    @field_validator("table")
    @classmethod
    def _qualified(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 2 or not all(_IDENTIFIER.match(p) for p in parts):
            raise ValueError("table must be database.table, letters, digits and underscores")
        return value

    @field_validator("key_columns", "text_columns")
    @classmethod
    def _identifiers(cls, values: list[str]) -> list[str]:
        bad = [v for v in values if not _IDENTIFIER.match(v)]
        if bad:
            raise ValueError(f"not valid column names: {bad}")
        return values

    @field_validator("timestamp_column")
    @classmethod
    def _identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.match(value):
            raise ValueError(f"not a valid column name: {value!r}")
        return value

    @field_validator("filters")
    @classmethod
    def _filter_columns(cls, values: dict[str, Any]) -> dict[str, Any]:
        bad = [k for k in values if not _IDENTIFIER.match(k)]
        if bad:
            raise ValueError(f"not valid column names: {bad}")
        return values


class CrawlConfig(_Model):
    enabled: bool = True
    settings_version: int | None = Field(
        None, description="Crawler settings version; empty means the active version"
    )
    schedule: Literal["manual"] = "manual"


CONNECTOR_TYPES: dict[str, dict[str, Any]] = {
    "helios_ds": {
        "label": "Helios-DS dataset",
        "scope": HeliosDsScope,
        "connection_help": "Workbench data connection for the artifact bucket, e.g. S3 Object Store",
    },
    "object_store": {
        "label": "Object store (S3, Ozone)",
        "scope": ObjectStoreScope,
        "connection_help": "Workbench data connection name, e.g. S3 Object Store",
    },
    "table_rows": {
        "label": "Lakehouse table rows",
        "scope": TableRowsScope,
        "connection_help": "impala (the Helios Impala settings, read as the crawler identity)",
    },
}


def is_crawlable(connector: str) -> bool:
    return connector in CONNECTOR_TYPES


def validate_scope(connector: str, scope: dict[str, Any]) -> dict[str, Any]:
    """The scope, validated and with defaults filled in; ValueError if invalid."""
    if connector not in CONNECTOR_TYPES:
        raise ValueError(f"{connector!r} is not a crawlable connector type")
    model = CONNECTOR_TYPES[connector]["scope"]
    return model.model_validate(scope).model_dump(mode="json")


def catalog() -> list[dict[str, Any]]:
    """Connector types with their scope JSON schemas, for the UI's forms."""
    return [
        {
            "connector": name,
            "label": spec["label"],
            "connection_help": spec["connection_help"],
            "scope_schema": spec["scope"].model_json_schema(),
        }
        for name, spec in CONNECTOR_TYPES.items()
    ]
