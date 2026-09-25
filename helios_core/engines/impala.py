"""Impala adapter: connects to a Cloudera Data Warehouse Impala Virtual Warehouse over HTTPS with LDAP auth."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

from ..config import ImpalaConfig
from .base import Engine, QueryResult


class ImpalaEngine(Engine):
    name = "impala"
    sqlglot_dialect = "hive"   # SQLGlot has no dedicated Impala dialect; Hive is the closest and is post-processed by the compiler

    def __init__(self, cfg: ImpalaConfig):
        self.cfg = cfg

    def _connect(self, delegated_user: str | None = None):
        from impala.dbapi import connect
        http_path = self.cfg.http_path
        if delegated_user:
            if not self.cfg.proxy_delegation:
                raise PermissionError("Impala proxy-user delegation is disabled")
            path, separator, query = http_path.partition("?")
            parameters = dict(parse_qsl(query, keep_blank_values=True))
            parameters["doAs"] = delegated_user
            http_path = f"{path}?{urlencode(parameters)}"
        return connect(host=self.cfg.host, port=self.cfg.port, database=self.cfg.database,
                       user=self.cfg.user, password=self.cfg.password,
                       auth_mechanism="LDAP", use_ssl=True,
                       use_http_transport=True, http_path=http_path)

    def query(
        self,
        sql: str,
        limit: int | None = 1000,
        *,
        delegated_user: str | None = None,
    ) -> QueryResult:
        conn = self._connect(delegated_user)
        try:
            cur = conn.cursor()
            if delegated_user:
                cur.execute("SELECT EFFECTIVE_USER()")
                effective = cur.fetchone()
                if not effective or effective[0] != delegated_user:
                    raise PermissionError(
                        "Impala did not enforce the delegated SSO identity"
                    )
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
