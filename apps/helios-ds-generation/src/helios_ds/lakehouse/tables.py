"""Lakehouse table registry: which record model backs which Iceberg table.

The tables live in the same lakehouse as ``tpcds`` (Hive Metastore + Iceberg),
created and written through Impala. The Impala DDL in
``schemas/lakehouse_impala.sql`` and the PyIceberg schemas used by offline tests
are both derived from the record models in ``helios_ds.schemas``, so they can
never drift apart. Mapping rules:

- str -> string, int -> long, float -> double, bool -> boolean
- dict/list fields -> string holding canonical JSON (keeps tables readable
  from Impala without nested types)
- every column is optional in Iceberg (Impala creates optional columns); the
  pydantic models enforce which fields are required
"""

import types
import typing
from dataclasses import dataclass
from typing import Dict, List, Tuple, Type

from pydantic import BaseModel
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    DoubleType,
    IcebergType,
    LongType,
    NestedField,
    StringType,
)

from .. import schemas

HELIOS_DS = "helios_ds"
HELIOS_GROUND_TRUTH = "helios_ground_truth"
NAMESPACES = (HELIOS_DS, HELIOS_GROUND_TRUTH)


@dataclass(frozen=True)
class TableSpec:
    namespace: str
    name: str
    model: Type[BaseModel]

    @property
    def identifier(self) -> Tuple[str, str]:
        return (self.namespace, self.name)

    @property
    def full_name(self) -> str:
        return f"{self.namespace}.{self.name}"


TABLES: Dict[str, TableSpec] = {
    spec.full_name: spec
    for spec in (
        TableSpec(HELIOS_DS, "datasets", schemas.DatasetRecord),
        TableSpec(HELIOS_DS, "generation_runs", schemas.GenerationRunRecord),
        TableSpec(HELIOS_DS, "dataset_lifecycle", schemas.DatasetLifecycleRecord),
        TableSpec(HELIOS_DS, "scenario_plans", schemas.ScenarioPlanRecord),
        TableSpec(HELIOS_DS, "template_versions", schemas.TemplateVersionRecord),
        TableSpec(HELIOS_DS, "generation_jobs", schemas.GenerationJobRecord),
        TableSpec(HELIOS_DS, "job_events", schemas.JobEventRecord),
        TableSpec(HELIOS_DS, "review_marks", schemas.ReviewMarkRecord),
        TableSpec(HELIOS_DS, "artifacts", schemas.ArtifactRecord),
        TableSpec(HELIOS_DS, "artifact_sources", schemas.ArtifactSourceRecord),
        TableSpec(HELIOS_DS, "source_principals", schemas.SourcePrincipalRecord),
        TableSpec(HELIOS_DS, "source_acl_bindings", schemas.SourceACLBindingRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "entities", schemas.TruthEntityRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "entity_mentions", schemas.TruthEntityMentionRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "relationships", schemas.TruthRelationshipRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "claims", schemas.TruthClaimRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "evidence", schemas.TruthEvidenceRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "expected_queries", schemas.ExpectedQueryRecord),
        TableSpec(HELIOS_GROUND_TRUTH, "expected_results", schemas.ExpectedResultRecord),
    )
}

_SCALARS: Dict[type, Tuple[IcebergType, str]] = {
    str: (StringType(), "STRING"),
    int: (LongType(), "BIGINT"),
    float: (DoubleType(), "DOUBLE"),
    bool: (BooleanType(), "BOOLEAN"),
}


def _unwrap_optional(annotation: object) -> object:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def is_json_field(annotation: object) -> bool:
    """True for dict/list fields, which are stored as JSON strings."""
    base = _unwrap_optional(annotation)
    return base in (dict, list) or typing.get_origin(base) in (dict, list)


def _column_types(annotation: object) -> Tuple[IcebergType, str]:
    if is_json_field(annotation):
        return _SCALARS[str]
    base = _unwrap_optional(annotation)
    if base in _SCALARS:
        return _SCALARS[base]  # type: ignore[index]
    raise TypeError(f"unsupported lakehouse column type {annotation!r}")


def iceberg_schema(spec: TableSpec) -> Schema:
    fields = [
        NestedField(
            field_id=i, name=name, field_type=_column_types(f.annotation)[0], required=False
        )
        for i, (name, f) in enumerate(spec.model.model_fields.items(), start=1)
    ]
    return Schema(*fields)


@dataclass(frozen=True)
class SqlDialect:
    """How to spell namespace and table creation for one SQL engine."""

    name: str
    create_namespace: str  # format string with {namespace}
    table_suffix: str  # appended after the column list
    add_column: str  # format string with {table}, {column}, {type}


IMPALA = SqlDialect(
    "impala",
    "CREATE DATABASE IF NOT EXISTS {namespace}",
    "STORED AS ICEBERG TBLPROPERTIES ('format-version'='2')",
    "ALTER TABLE {table} ADD COLUMNS ({column} {type})",
)
# Stand-in engine for offline tests of the SQL sink.
DUCKDB = SqlDialect(
    "duckdb",
    "CREATE SCHEMA IF NOT EXISTS {namespace}",
    "",
    "ALTER TABLE {table} ADD COLUMN {column} {type}",
)


def column_names(spec: TableSpec) -> Tuple[str, ...]:
    return tuple(spec.model.model_fields)


def column_sql_types(spec: TableSpec) -> List[Tuple[str, str]]:
    """(column, SQL type) pairs, in model order."""
    return [(name, _column_types(f.annotation)[1]) for name, f in spec.model.model_fields.items()]


def column_iceberg_types(spec: TableSpec) -> List[Tuple[str, IcebergType]]:
    return [(name, _column_types(f.annotation)[0]) for name, f in spec.model.model_fields.items()]


def ddl_statements(dialect: SqlDialect = IMPALA) -> List[str]:
    """CREATE statements for every namespace and table, in dependency order."""
    statements = []
    for namespace in NAMESPACES:
        statements.append(dialect.create_namespace.format(namespace=namespace))
        for spec in (s for s in TABLES.values() if s.namespace == namespace):
            columns = ",\n".join(
                f"  {name} {_column_types(f.annotation)[1]}"
                for name, f in spec.model.model_fields.items()
            )
            suffix = f"\n{dialect.table_suffix}" if dialect.table_suffix else ""
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {spec.full_name} (\n{columns}\n){suffix}"
            )
    statements.append(CRAWLABLE_ARTIFACTS_VIEW)
    return statements


# What the Helios crawler may read (crawler task CR-0d): artifacts of READY
# datasets only, with neutral columns. scenario_id and template IDs are left out
# because they reveal which documents belong to one story; the crawler must
# discover that. Ranger grants the crawler SELECT on this view, nothing else in
# helios_ds and nothing in helios_ground_truth.
CRAWLABLE_ARTIFACTS = "helios_ds.crawlable_artifacts"
CRAWLABLE_COLUMNS = (
    "artifact_id",
    "dataset_id",
    "artifact_type",
    "mime_type",
    "source_locator",
    "sha256",
    "size_bytes",
    "semantic_timestamp",
)
CRAWLABLE_ARTIFACTS_VIEW = (
    f"CREATE VIEW IF NOT EXISTS {CRAWLABLE_ARTIFACTS} AS\n"
    "SELECT " + ", ".join(f"a.{c}" for c in CRAWLABLE_COLUMNS) + "\n"
    "FROM helios_ds.artifacts a\n"
    "JOIN (\n"
    "  SELECT dataset_id, state FROM (\n"
    "    SELECT dataset_id, state,\n"
    "           ROW_NUMBER() OVER (PARTITION BY dataset_id ORDER BY event_seq DESC) AS rn\n"
    "    FROM helios_ds.dataset_lifecycle\n"
    "  ) latest WHERE rn = 1\n"
    ") lifecycle ON lifecycle.dataset_id = a.dataset_id\n"
    "WHERE lifecycle.state = 'READY'"
)


def impala_ddl() -> str:
    """The Impala DDL as a script, for Hue/impala-shell."""
    lines = [
        "-- Generated by `python -m helios_ds.lakehouse`; do not edit by hand.",
        "-- Tests fail if this file is out of date with helios_ds.schemas.",
    ]
    for statement in ddl_statements(IMPALA):
        lines += ["", f"{statement};"]
    return "\n".join(lines) + "\n"
