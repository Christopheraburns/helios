"""TPC-DS repository adapters and source snapshot fingerprinting.

The scenario eligibility SQL is plain SQL that runs unchanged on both backends:

- ``ImpalaTpcds``: the ``tpcds`` Iceberg tables in the lakehouse, queried
  through the Impala Virtual Warehouse (Workbench). The Helios project has no
  direct access to the storage underneath, so all access goes through Impala.
- ``DuckDbTpcds``: a DuckDB database file, or data generated in-process with
  DuckDB's ``dsdgen`` (local development and tests).

Query results are returned as *canonical records*: string values right-trimmed
(engines differ on CHAR padding), dates as ISO strings, decimals as exact
strings. Callers must sort results by business key; SQL row order is never
relied on.
"""

import datetime as dt
import decimal
import json
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import duckdb

from .ids import hash_parts


def canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.rstrip()
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    raise TypeError(f"unsupported TPC-DS value type {type(value).__name__}")


class TpcdsRepository:
    """Runs eligibility SQL against TPC-DS and describes the source tables."""

    backend = "abstract"

    def _execute(self, sql: str) -> Tuple[List[str], List[Tuple[Any, ...]]]:
        raise NotImplementedError

    def query(self, sql: str) -> List[Dict[str, Any]]:
        columns, rows = self._execute(sql)
        return [{c: canonical_value(v) for c, v in zip(columns, row, strict=True)} for row in rows]

    def columns(self, sql: str) -> List[str]:
        return self._execute(f"SELECT * FROM ({sql}) AS q LIMIT 0")[0]

    def table_identity(self, table: str) -> Dict[str, Any]:
        raise NotImplementedError

    def fingerprint(self, tables: Iterable[str]) -> Dict[str, Any]:
        names = sorted(set(tables))
        return {
            "backend": self.backend,
            "tables": {name: self.table_identity(name) for name in names},
        }


def fingerprint_hash(fingerprint: Dict[str, Any]) -> str:
    return hash_parts(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")))


class DuckDbTpcds(TpcdsRepository):
    """TPC-DS tables that live in a DuckDB database.

    The content hash uses DuckDB's own row formatting, so it is stable for a
    pinned DuckDB version (the runtime image pins it), not across versions.
    """

    backend = "duckdb"

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def _execute(self, sql: str) -> Tuple[List[str], List[Tuple[Any, ...]]]:
        relation = self.con.sql(sql)
        return relation.columns, relation.fetchall()

    @classmethod
    def open(cls, path: str) -> "DuckDbTpcds":
        return cls(duckdb.connect(path, read_only=True))

    @classmethod
    def generate(cls, scale_factor: float, path: Optional[str] = None) -> "DuckDbTpcds":
        """Generate TPC-DS with dsdgen (needs the DuckDB ``tpcds`` extension).

        The runtime image preinstalls the extension under
        DUCKDB_EXTENSION_DIRECTORY so this works without network access.
        """
        con = duckdb.connect(path or ":memory:")
        extension_dir = os.environ.get("DUCKDB_EXTENSION_DIRECTORY")
        if extension_dir:
            con.execute("SET extension_directory = ?", [extension_dir])
        con.sql("INSTALL tpcds; LOAD tpcds;")
        con.sql(f"CALL dsdgen(sf={float(scale_factor)!r})")
        return cls(con)

    def table_identity(self, table: str) -> Dict[str, Any]:
        schema = self.con.sql(f"DESCRIBE {table}").fetchall()
        row = self.con.sql(
            f"SELECT count(*), bit_xor(md5_number(t::VARCHAR)) FROM {table} AS t"
        ).fetchone()
        assert row is not None
        rows, content = row
        return {
            "rows": rows,
            "schema_sha256": hash_parts([[name, type_] for name, type_, *_ in schema]),
            "content_sha256": hash_parts(str(content)),
        }


class ImpalaTpcds(TpcdsRepository):
    """TPC-DS Iceberg tables queried through Impala (a DB-API connection).

    The source identity is each table's current Iceberg snapshot (from
    ``DESCRIBE HISTORY``), row count and column types.
    """

    backend = "impala"

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect
        self._connection: Any = None

    def _execute(self, sql: str) -> Tuple[List[str], List[Tuple[Any, ...]]]:
        if self._connection is None:
            self._connection = self._connect()
        cursor = self._connection.cursor()
        cursor.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return columns, [tuple(r) for r in cursor.fetchall()]

    def table_identity(self, table: str) -> Dict[str, Any]:
        _, schema = self._execute(f"DESCRIBE {table}")
        _, count = self._execute(f"SELECT count(*) FROM {table}")
        history_columns, history = self._execute(f"DESCRIBE HISTORY {table}")
        snapshots = [dict(zip(history_columns, row, strict=True)) for row in history]
        current = [s for s in snapshots if str(s.get("is_current_ancestor")).lower() == "true"]
        latest = max(current or snapshots, key=lambda s: str(s["creation_time"]), default=None)
        return {
            "rows": int(count[0][0]),
            "schema_sha256": hash_parts([[str(r[0]), str(r[1]).lower()] for r in schema]),
            "snapshot_id": int(latest["snapshot_id"]) if latest else None,
        }
