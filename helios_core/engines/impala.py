"""Impala adapter: connects to a Cloudera Data Warehouse Impala Virtual Warehouse over HTTPS with LDAP auth."""
from __future__ import annotations

from ..config import ImpalaConfig
from .base import Engine, QueryResult


class ImpalaEngine(Engine):
    name = "impala"
    sqlglot_dialect = "hive"   # SQLGlot has no dedicated Impala dialect; Hive is the closest and is post-processed by the compiler

    def __init__(self, cfg: ImpalaConfig):
        self.cfg = cfg

    def _connect(self):
        from impala.dbapi import connect
        return connect(host=self.cfg.host, port=self.cfg.port, database=self.cfg.database,
                       user=self.cfg.user, password=self.cfg.password,
                       auth_mechanism="LDAP", use_ssl=True,
                       use_http_transport=True, http_path=self.cfg.http_path)

    def query(self, sql: str, limit: int | None = 1000) -> QueryResult:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            if cur.description is None:
                return QueryResult([], [])
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit) if limit else cur.fetchall()
            return QueryResult(cols, [tuple(r) for r in rows])
        finally:
            conn.close()

    def ping(self) -> bool:
        return self.query("SELECT 1").rows == [(1,)]

    def query_history(self, limit: int = 500) -> QueryResult:
        """Recent statements from Impala's query log, when the VW exposes it via the sys database."""
        return self.query(f"SELECT * FROM sys.impala_query_log ORDER BY start_time_utc DESC LIMIT {limit}", limit)
