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
import warnings
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence

import pyarrow as pa
from pydantic import BaseModel
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, BooleanExpression, EqualTo
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.table import Table

from .tables import (
    IMPALA,
    NAMESPACES,
    TABLES,
    SqlDialect,
    TableSpec,
    column_names,
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
    def _read_rows(self, spec: TableSpec, dataset_id: Optional[str]) -> List[Dict[str, Any]]: ...

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

    def read(self, table: str, dataset_id: Optional[str] = None) -> List[BaseModel]:
        """All rows of a table, or only one dataset's rows."""
        spec = self._spec(table)
        json_fields = self._json_fields(spec)
        rows = self._read_rows(spec, dataset_id)
        for row in rows:
            for name in json_fields:
                if row.get(name) is not None:
                    row[name] = json.loads(row[name])
        return [spec.model.model_validate(row) for row in rows]

    def read_dataset(self, table: str, dataset_id: str) -> List[BaseModel]:
        return self.read(table, dataset_id)

    def delete_dataset_rows(self, table: str, dataset_id: str) -> None:
        """Remove a dataset's rows from one table (used to clear a partial publish)."""
        self._delete_rows(self._spec(table), dataset_id)


class SqlLakehouseSink(LakehouseSink):
    """Lakehouse access through a DB-API connection (Impala in Workbench).

    Values are always bound as ``?`` parameters, so the driver does the quoting.
    Inserts are batched into multi-row VALUES statements.
    """

    MAX_ROWS_PER_INSERT = 500
    MAX_BYTES_PER_INSERT = 1_000_000

    def __init__(self, connect: Callable[[], Any], dialect: SqlDialect = IMPALA):
        self._connect = connect
        self.dialect = dialect
        self._connection: Any = None

    def _cursor(self) -> Any:
        if self._connection is None:
            self._connection = self._connect()
        return self._connection.cursor()

    def _execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
        cursor = self._cursor()
        cursor.execute(sql, list(params) if params is not None else None)
        return cursor

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def ensure_tables(self) -> None:
        for statement in ddl_statements(self.dialect):
            self._execute(statement)

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

    def _read_rows(self, spec: TableSpec, dataset_id: Optional[str]) -> List[Dict[str, Any]]:
        columns = column_names(spec)
        sql = f"SELECT {', '.join(columns)} FROM {spec.full_name}"
        if dataset_id is None:
            cursor = self._execute(sql)
        else:
            cursor = self._execute(f"{sql} WHERE dataset_id = ?", [dataset_id])
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _delete_rows(self, spec: TableSpec, dataset_id: str) -> None:
        # Impala supports DELETE on Iceberg v2 tables.
        self._execute(f"DELETE FROM {spec.full_name} WHERE dataset_id = ?", [dataset_id])


def _dataset_filter(dataset_id: str) -> BooleanExpression:
    # EqualTo's runtime constructor takes (term, literal); the mypy pydantic
    # plugin only sees its model fields.
    return EqualTo("dataset_id", dataset_id)  # type: ignore[call-arg,misc]


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
            self.catalog.create_table_if_not_exists(
                spec.identifier,
                schema=iceberg_schema(spec),
                properties={"format-version": "2"},
            )

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

    def _read_rows(self, spec: TableSpec, dataset_id: Optional[str]) -> List[Dict[str, Any]]:
        table = self._table(spec)
        table.refresh()
        row_filter = AlwaysTrue() if dataset_id is None else _dataset_filter(dataset_id)
        rows: List[Dict[str, Any]] = table.scan(row_filter=row_filter).to_arrow().to_pylist()
        return rows

    def _delete_rows(self, spec: TableSpec, dataset_id: str) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Delete operation did not match any records")
            self._table(spec).delete(_dataset_filter(dataset_id))
