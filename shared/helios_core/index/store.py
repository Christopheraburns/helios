"""A small helios_index store over a DB-API connection (impyla in Workbench,
DuckDB in tests). Values are always bound as ``?`` parameters; lists and dicts
are stored as canonical JSON and decoded on read.

Inserts are batched into multi-row VALUES statements: on Iceberg every INSERT
is a commit and a new data file, so one statement per row would be slow and
fragment the table.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from .tables import DUCKDB, IMPALA, TABLES, Dialect, columns, ddl_statements, json_columns

MAX_BATCH_ROWS = 500
MAX_BATCH_CHARS = 2_000_000  # stay well under Impala's statement size limit


class IndexStore:
    def __init__(self, connect: Callable[[], Any], dialect: Dialect = IMPALA):
        self._connect = connect
        self.dialect = dialect
        self._local = threading.local()  # DB-API connections are not thread-safe

    def _connection(self) -> Any:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = self._connect()
        return connection

    def _execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        cursor = self._connection().cursor()
        cursor.execute(sql, list(params) if params else None)
        return cursor

    def ensure_tables(self) -> None:
        """Create the namespace and tables, and add any columns missing from them."""
        for statement in ddl_statements(self.dialect):
            self._execute(statement)
        for table, model in TABLES.items():
            existing = {
                str(row[0]).lower() for row in self._execute(f"DESCRIBE {table}").fetchall()
            }
            for name, sql_type in columns(model):
                if name.lower() not in existing:
                    self._execute(
                        self.dialect.add_column.format(table=table, column=name, type=sql_type)
                    )

    def append(self, table: str, records: Sequence[BaseModel]) -> None:
        if not records:
            return
        model = TABLES[table]
        names = [name for name, _ in columns(model)]
        as_json = json_columns(model)
        placeholder = "(" + ", ".join("?" for _ in names) + ")"
        prefix = f"INSERT INTO {table} ({', '.join(names)}) VALUES "

        batch: list[list[Any]] = []
        size = 0

        def flush() -> None:
            nonlocal batch, size
            if batch:
                sql = prefix + ", ".join(placeholder for _ in batch)
                self._execute(sql, [value for row in batch for value in row])
            batch, size = [], 0

        for record in records:
            if not isinstance(record, model):
                raise TypeError(f"{table} takes {model.__name__}, got {type(record).__name__}")
            row = record.model_dump()
            values = [
                json.dumps(row[n], sort_keys=True, separators=(",", ":"))
                if n in as_json and row[n] is not None
                else row[n]
                for n in names
            ]
            row_size = sum(len(v) if isinstance(v, str) else 16 for v in values)
            if batch and (len(batch) >= MAX_BATCH_ROWS or size + row_size > MAX_BATCH_CHARS):
                flush()
            batch.append(values)
            size += row_size
        flush()

    def read(self, table: str, where: dict[str, Any] | None = None) -> list[BaseModel]:
        model = TABLES[table]
        names = [name for name, _ in columns(model)]
        as_json = json_columns(model)
        sql = f"SELECT {', '.join(names)} FROM {table}"
        filters = dict(where or {})
        unknown = set(filters) - set(names)
        if unknown:
            raise KeyError(f"{table} has no column(s) {sorted(unknown)}")
        if filters:
            sql += " WHERE " + " AND ".join(f"{column} = ?" for column in filters)
        rows = self._execute(sql, list(filters.values()) or None).fetchall()
        records = []
        for row in rows:
            data = dict(zip(names, row, strict=True))
            for name in as_json:
                if isinstance(data.get(name), str):
                    data[name] = json.loads(data[name])
            records.append(model.model_validate(data))
        return records

    def delete_run(self, crawl_run_id: str, tables: Sequence[str]) -> None:
        """Remove one crawl run's rows (to clear a failed or partial run)."""
        for table in tables:
            self._execute(f"DELETE FROM {table} WHERE crawl_run_id = ?", [crawl_run_id])


def impala_index_store() -> IndexStore | None:
    """The Workbench store, from the Helios Impala settings; None if not configured."""
    from ..config import impala_config
    from ..engines.impala import ImpalaEngine

    config = impala_config()
    if config is None:
        return None
    return IndexStore(ImpalaEngine(config).connect, IMPALA)


def duckdb_index_store(path: str = ":memory:") -> IndexStore:
    import duckdb

    connection = duckdb.connect(path)
    return IndexStore(lambda: connection, DUCKDB)
