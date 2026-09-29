"""DuckDB adapter for local development and tests: runs the same catalog/profiler code without a warehouse.
Translates the few Impala idioms helios uses (backtick quoting, NDV, SHOW TABLES IN)."""
from __future__ import annotations

import re

from .base import Engine, QueryResult


class DuckDBEngine(Engine):
    name = "duckdb"
    sqlglot_dialect = "duckdb"

    def __init__(self, path: str = ":memory:"):
        import duckdb
        self.con = duckdb.connect(path)

    def _translate(self, sql: str) -> str:
        sql = sql.replace("`", '"')
        sql = re.sub(r"\bNDV\(", "approx_count_distinct(", sql, flags=re.I)
        m = re.match(r"\s*SHOW TABLES IN (\w+)\s*$", sql, flags=re.I)
        if m:
            return f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{m.group(1)}' ORDER BY 1"
        m = re.match(r"\s*DESCRIBE (\w+)\.(\w+)\s*$", sql, flags=re.I)
        if m:
            return (f"SELECT column_name, data_type, '' FROM information_schema.columns "
                    f"WHERE table_schema = '{m.group(1)}' AND table_name = '{m.group(2)}' ORDER BY ordinal_position")
        if re.match(r"\s*SHOW TABLE STATS", sql, flags=re.I):
            raise RuntimeError("no table stats in duckdb")
        return sql

    def query(self, sql: str, limit: int | None = 1000) -> QueryResult:
        cur = self.con.execute(self._translate(sql))
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit) if limit else cur.fetchall()
        return QueryResult(cols, [tuple(r) for r in rows])

    def ping(self) -> bool:
        return self.query("SELECT 1").rows == [(1,)]
