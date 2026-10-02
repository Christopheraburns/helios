"""Table registry and DDL for helios_index, derived from the record models."""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .records import (
    AssetRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    CrawlerSettingsActivationRecord,
    CrawlerSettingsRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    EntityRecord,
    MentionRecord,
    OntologyActivationRecord,
    OntologyVersionRecord,
    RelationshipRecord,
    SegmentRecord,
)

NAMESPACE = "helios_index"

TABLES: dict[str, type[BaseModel]] = {
    f"{NAMESPACE}.ontology_versions": OntologyVersionRecord,
    f"{NAMESPACE}.ontology_activations": OntologyActivationRecord,
    f"{NAMESPACE}.crawl_runs": CrawlRunRecord,
    f"{NAMESPACE}.assets": AssetRecord,
    f"{NAMESPACE}.segments": SegmentRecord,
    f"{NAMESPACE}.mentions": MentionRecord,
    f"{NAMESPACE}.entities": EntityRecord,
    f"{NAMESPACE}.entity_links": EntityLinkRecord,
    f"{NAMESPACE}.relationships": RelationshipRecord,
    f"{NAMESPACE}.claims": ClaimRecord,
    f"{NAMESPACE}.claim_evidence": ClaimEvidenceRecord,
    f"{NAMESPACE}.crawler_settings": CrawlerSettingsRecord,
    f"{NAMESPACE}.crawler_settings_activations": CrawlerSettingsActivationRecord,
}

# Tables written per crawl run (rows carry crawl_run_id).
RUN_TABLES = tuple(name for name, model in TABLES.items() if "crawl_run_id" in model.model_fields)

_SQL_TYPES = {str: "STRING", int: "BIGINT", float: "DOUBLE", bool: "BOOLEAN"}


@dataclass(frozen=True)
class Dialect:
    name: str
    create_namespace: str
    table_suffix: str
    add_column: str


IMPALA = Dialect(
    "impala",
    "CREATE DATABASE IF NOT EXISTS {namespace}",
    " STORED AS ICEBERG TBLPROPERTIES ('format-version'='2')",
    "ALTER TABLE {table} ADD COLUMNS ({column} {type})",
)
DUCKDB = Dialect(
    "duckdb",
    "CREATE SCHEMA IF NOT EXISTS {namespace}",
    "",
    "ALTER TABLE {table} ADD COLUMN {column} {type}",
)


def _sql_type(annotation: Any) -> str:
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _sql_type(inner[0])
    if annotation in _SQL_TYPES:
        return _SQL_TYPES[annotation]
    return "STRING"  # lists / dicts are stored as canonical JSON


def columns(model: type[BaseModel]) -> list[tuple[str, str]]:
    return [(name, _sql_type(field.annotation)) for name, field in model.model_fields.items()]


def json_columns(model: type[BaseModel]) -> set[str]:
    """Fields stored as canonical JSON strings (lists and dicts)."""
    return {name for name, field in model.model_fields.items() if _is_json(field.annotation)}


def _is_json(annotation: Any) -> bool:
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        return any(_is_json(a) for a in typing.get_args(annotation) if a is not type(None))
    return typing.get_origin(annotation) in (list, dict) or annotation in (list, dict)


def ddl_statements(dialect: Dialect = IMPALA) -> list[str]:
    statements = [dialect.create_namespace.format(namespace=NAMESPACE)]
    for table, model in TABLES.items():
        cols = ",\n  ".join(f"{name} {sql}" for name, sql in columns(model))
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {table} (\n  {cols}\n){dialect.table_suffix}"
        )
    return statements


# Impala reserved words (Impala 4), which cannot be column names unescaped.
# Checked by tests; earlier regressions: COMMENT (Helios-DS), FIELDS (CR-3).
IMPALA_RESERVED = frozenset(
    """
    add aggregate all alter analytic and anti api_version array as asc avro between bigint
    binary block_size boolean by cached case cascade cast change char class close_fn column
    columns comment compression compute copy create cross current data database databases
    date datetime decimal default delete delimited desc describe distinct div double drop
    else encoding end escaped exists explain extended external false fields fileformat files
    finalize_fn first float following for format formatted from full function functions
    grant group hash having if ilike in incremental init_fn inner inpath insert int integer
    intermediate interval into invalidate iregexp is join kudu last left like limit lines
    load location map merge_fn metadata not null nulls offset on or order outer over
    overwrite parquet partition partitioned partitions preceding prepare_fn primary produced
    purge range rcfile real recover refresh regexp rename repeatable replace replication
    restrict returns revoke right rlike role roles row rows schema schemas select semi
    sequencefile serdeproperties serialize_fn set show smallint sort stats stored
    straight_join string struct symbol table tables tablesample tblproperties terminated
    textfile then timestamp tinyint to true truncate unbounded uncached union unknown update
    update_fn upsert use using values varchar view when where with
    """.split()
)
