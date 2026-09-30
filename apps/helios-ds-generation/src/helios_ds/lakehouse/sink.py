"""LakehouseSink: the only path for lakehouse writes (spec: "Use a LakehouseSink
abstraction for all Iceberg writes").

helios_ds.* and helios_ground_truth.* live in the same lakehouse as tpcds. The
Helios project has no direct access to the storage under that lakehouse, so in
Workbench every read and write goes through a Cloudera service:

- ``SqlLakehouseSink`` over Impala (``lakehouse.impala``): Workbench.
- ``IcebergCatalogSink`` over a PyIceberg SQL catalog (SQLite/PostgreSQL):
  offline unit tests and CI only, as the spec allows.

Both expose the same logical tables and records.
"""

import json
import threading
import warnings
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pyarrow as pa
from pydantic import BaseModel
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, And, BooleanExpression, EqualTo
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.table import Table

from .tables import (
    IMPALA,
    NAMESPACES,
    TABLES,
    SqlDialect,
    TableSpec,
    column_iceberg_types,
    column_names,
    column_sql_types,
    ddl_statements,
    iceberg_schema,
    is_json_field,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class LakehouseSink(ABC):
    @abstractmethod
    def ensure_tables(self) -> None:
        """Create any missing namespace or table. Existing tables are left as they are."""

    @abstractmethod
    def _append_rows(self, spec: TableSpec, rows: List[Dict[str, Any]]) -> None: ...

    @abstractmethod
    def _read_rows(self, spec: TableSpec, where: Dict[str, Any]) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def _delete_rows(self, spec: TableSpec, dataset_id: str) -> None: ...

    def _spec(self, table: str) -> TableSpec:
        try:
            return TABLES[table]
        except KeyError:
            raise KeyError(f"unknown lakehouse table {table!r}") from None

    @staticmethod
    def _json_fields(spec: TableSpec) -> List[str]:
        return [n for n, f in spec.model.model_fields.items() if is_json_field(f.annotation)]

    def append(self, table: str, records: Sequence[BaseModel]) -> None:
        spec = self._spec(table)
        if not records:
            return
        wrong = {type(r).__name__ for r in records if not isinstance(r, spec.model)}
        if wrong:
            raise TypeError(f"{table} takes {spec.model.__name__} records, got {sorted(wrong)}")
        json_fields = self._json_fields(spec)
        rows = []
        for record in records:
            row = record.model_dump(mode="json")
            for name in json_fields:
                if row[name] is not None:
                    row[name] = canonical_json(row[name])
            rows.append(row)
        self._append_rows(spec, rows)

    def read(
        self,
        table: str,
        dataset_id: Optional[str] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[BaseModel]:
        """Rows of a table, optionally filtered by column equality (``where``);
        ``dataset_id`` is shorthand for ``where={"dataset_id": ...}``."""
        spec = self._spec(table)
        filters = dict(where or {})
        if dataset_id is not None:
            filters["dataset_id"] = dataset_id
        unknown = sorted(set(filters) - set(spec.model.model_fields))
        if unknown:
            raise KeyError(f"{table} has no column(s) {unknown}")
        json_fields = self._json_fields(spec)
        rows = self._read_rows(spec, filters)
        for row in rows:
            for name in json_fields:
                if row.get(name) is not None:
                    row[name] = json.loads(row[name])
        return [spec.model.model_validate(row) for row in rows]

    def read_dataset(self, table: str, dataset_id: str) -> List[BaseModel]:
        return self.read(table, dataset_id)

    def count_by(self, table: str, columns: Sequence[str]) -> Dict[Tuple[Any, ...], int]:
        """Row counts grouped by ``columns`` (e.g. artifacts per dataset and type)."""
        spec = self._spec(table)
        unknown = sorted(set(columns) - set(spec.model.model_fields))
        if unknown:
            raise KeyError(f"{table} has no column(s) {unknown}")
        return self._count_rows(spec, list(columns))

    def _count_rows(self, spec: TableSpec, columns: List[str]) -> Dict[Tuple[Any, ...], int]:
        counts: Dict[Tuple[Any, ...], int] = {}
        for row in self._read_rows(spec, {}):
            key = tuple(row[c] for c in columns)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def delete_dataset_rows(self, table: str, dataset_id: str) -> None:
        """Remove a dataset's rows from one table (used to clear a partial publish)."""
        self._delete_rows(self._spec(table), dataset_id)


class SqlLakehouseSink(LakehouseSink):
    """Lakehouse access through a DB-API connection (Impala in Workbench).

    Values are always bound as ``?`` parameters, so the driver does the quoting.
    Inserts are batched into multi-row VALUES statements. DB-API connections
    (impyla included) are not thread-safe, so each thread gets its own; the API
    serves requests from a thread pool.
    """

    MAX_ROWS_PER_INSERT = 500
    MAX_BYTES_PER_INSERT = 1_000_000

    def __init__(self, connect: Callable[[], Any], dialect: SqlDialect = IMPALA):
        self._connect = connect
        self.dialect = dialect
        self._local = threading.local()

    def _cursor(self) -> Any:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = self._connect()
        return connection.cursor()

    def _execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
        cursor = self._cursor()
        cursor.execute(sql, list(params) if params is not None else None)
        return cursor

    def close(self) -> None:
        """Close the calling thread's connection."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def ensure_tables(self) -> None:
        """Create missing namespaces and tables, then add any columns the record
        models gained since a table was created (additive only: never drops)."""
        for statement in ddl_statements(self.dialect):
            self._execute(statement)
        for spec in TABLES.values():
            cursor = self._execute(f"DESCRIBE {spec.full_name}")
            existing = {str(row[0]).lower() for row in cursor.fetchall()}
            for column, sql_type in column_sql_types(spec):
                if column.lower() not in existing:
                    self._execute(
                        self.dialect.add_column.format(
                            table=spec.full_name, column=column, type=sql_type
                        )
                    )

    def _append_rows(self, spec: TableSpec, rows: List[Dict[str, Any]]) -> None:
        columns = column_names(spec)
        placeholder = "(" + ", ".join("?" for _ in columns) + ")"
        batch: List[Dict[str, Any]] = []
        batch_bytes = 0
        for row in rows:
            size = sum(len(str(v)) for v in row.values())
            if batch and (
                len(batch) >= self.MAX_ROWS_PER_INSERT
                or batch_bytes + size > self.MAX_BYTES_PER_INSERT
            ):
                self._insert(spec, columns, placeholder, batch)
                batch, batch_bytes = [], 0
            batch.append(row)
            batch_bytes += size
        if batch:
            self._insert(spec, columns, placeholder, batch)

    def _insert(
        self,
        spec: TableSpec,
        columns: Sequence[str],
        placeholder: str,
        batch: List[Dict[str, Any]],
    ) -> None:
        sql = f"INSERT INTO {spec.full_name} ({', '.join(columns)}) VALUES " + ", ".join(
            placeholder for _ in batch
        )
        self._execute(sql, [row[c] for row in batch for c in columns])

    def _read_rows(self, spec: TableSpec, where: Dict[str, Any]) -> List[Dict[str, Any]]:
        columns = column_names(spec)
        sql = f"SELECT {', '.join(columns)} FROM {spec.full_name}"
        if where:  # column names were checked against the record model
            sql += " WHERE " + " AND ".join(f"{column} = ?" for column in where)
        cursor = self._execute(sql, list(where.values()) if where else None)
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _count_rows(self, spec: TableSpec, columns: List[str]) -> Dict[Tuple[Any, ...], int]:
        # Column names were checked against the record model.
        group = ", ".join(columns)
        cursor = self._execute(
            f"SELECT {group}, COUNT(*) FROM {spec.full_name} GROUP BY {group}", None
        )
        return {tuple(row[:-1]): int(row[-1]) for row in cursor.fetchall()}

    def _delete_rows(self, spec: TableSpec, dataset_id: str) -> None:
        # Impala supports DELETE on Iceberg v2 tables.
        self._execute(f"DELETE FROM {spec.full_name} WHERE dataset_id = ?", [dataset_id])


def _equals(column: str, value: Any) -> BooleanExpression:
    # EqualTo's runtime constructor takes (term, literal); the mypy pydantic
    # plugin only sees its model fields.
    return EqualTo(column, value)  # type: ignore[call-arg,misc]


def _dataset_filter(dataset_id: str) -> BooleanExpression:
    return _equals("dataset_id", dataset_id)


class IcebergCatalogSink(LakehouseSink):
    """PyIceberg catalog sink, for offline tests and CI (SQLite/PostgreSQL SQL catalog)."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._tables: Dict[str, Table] = {}

    def ensure_tables(self) -> None:
        for namespace in NAMESPACES:
            try:
                self.catalog.create_namespace(namespace)
            except NamespaceAlreadyExistsError:
                pass
        for spec in TABLES.values():
            table = self.catalog.create_table_if_not_exists(
                spec.identifier,
                schema=iceberg_schema(spec),
                properties={"format-version": "2"},
            )
            existing = {f.name for f in table.schema().fields}
            missing = [(n, t) for n, t in column_iceberg_types(spec) if n not in existing]
            if missing:  # additive schema evolution for tables created earlier
                with table.update_schema() as update:
                    for name, field_type in missing:
                        update.add_column(name, field_type)
                self._tables.pop(spec.full_name, None)

    def _table(self, spec: TableSpec) -> Table:
        if spec.full_name not in self._tables:
            try:
                self._tables[spec.full_name] = self.catalog.load_table(spec.identifier)
            except NoSuchTableError as exc:
                raise NoSuchTableError(
                    f"{spec.full_name} does not exist; run ensure_tables() first"
                ) from exc
        return self._tables[spec.full_name]

    def _append_rows(self, spec: TableSpec, rows: List[Dict[str, Any]]) -> None:
        table = self._table(spec)
        table.append(pa.Table.from_pylist(rows, schema=schema_to_pyarrow(table.schema())))

    def _read_rows(self, spec: TableSpec, where: Dict[str, Any]) -> List[Dict[str, Any]]:
        table = self._table(spec)
        table.refresh()
        row_filter: BooleanExpression = AlwaysTrue()
        for column, value in where.items():
            row_filter = And(row_filter, _equals(column, value))
        rows: List[Dict[str, Any]] = table.scan(row_filter=row_filter).to_arrow().to_pylist()
        return rows

    def _delete_rows(self, spec: TableSpec, dataset_id: str) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Delete operation did not match any records")
            self._table(spec).delete(_dataset_filter(dataset_id))
