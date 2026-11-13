"""Trino engine (``engine = "trino"``), with the ``[trino]`` extra.

    [connections.lake]
    engine = "trino"
    host = "trino.example.com"
    catalog = "hive"
    identity = "impersonate"
    user = "helios"            # the service user
    password = "..."           # basic authentication over https; omit on an open cluster

Identity modes: ``service`` runs every query as ``user``. ``impersonate`` authenticates
as ``user`` and asks Trino to run the session as the caller (Trino's impersonation
rules decide), then checks ``current_user``. ``token`` authenticates with the caller's
JWT and needs no service user. Catalog lookups use the connection's catalog's
information_schema; Helios's own work (harvest, profile) runs as the service user.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationInfo, model_validator
from sqlglot import exp

from .._optional import missing_extra
from ..caller import Caller, IdentityError, IdentityMode, ImpersonationError
from .base import Engine, QueryResult

try:
    import trino
except ModuleNotFoundError as exc:
    raise missing_extra("the Trino engine", "trino", exc) from exc

if TYPE_CHECKING:
    from ..settings import ConnectionSettings


class TrinoOptions(BaseModel):
    """Options of an ``engine = "trino"`` connection."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str
    port: int | None = None             # 443 for https, 8080 for http
    catalog: str
    schema_: str | None = Field(default=None, alias="schema")  # "schema" would shadow BaseModel.schema
    user: str | None = None             # the service user; optional only in token mode
    password: SecretStr | None = None   # basic authentication; needs https
    http_scheme: Literal["https", "http"] = "https"
    verify: bool = True

    @model_validator(mode="after")
    def _credentials(self, info: ValidationInfo) -> TrinoOptions:
        identity = (info.context or {}).get("identity", IdentityMode.SERVICE)
        if self.user is None and identity is not IdentityMode.TOKEN:
            raise ValueError(f"a Trino connection in {identity.value} mode needs user")
        if self.password is not None and self.http_scheme != "https":
            raise ValueError("a Trino password needs https")
        return self


class TrinoIdentityError(ImpersonationError):
    """Trino did not run the query as the impersonated caller."""


class TrinoEngine(Engine):
    name = "trino"
    sqlglot_dialect = "trino"
    identity_modes = frozenset(IdentityMode)

    def __init__(self, options: TrinoOptions, identity_mode: IdentityMode | str = IdentityMode.SERVICE):
        super().__init__(identity_mode)
        self.options = options

    @classmethod
    def from_connection(cls, connection: ConnectionSettings) -> TrinoEngine:
        options = TrinoOptions.model_validate(connection.options, context={"identity": connection.identity})
        return cls(options, connection.identity)

    def _connect(self, caller: Caller | None):
        self.check_caller(caller)
        o = self.options
        common = dict(host=o.host, port=o.port or (443 if o.http_scheme == "https" else 8080),
                      catalog=o.catalog, schema=o.schema_, http_scheme=o.http_scheme, verify=o.verify,
                      source="helios")
        if caller is not None and self.identity_mode is IdentityMode.TOKEN:
            assert caller.token is not None  # check_caller refused a caller without one
            return trino.dbapi.connect(**common, user=caller.username,
                                       auth=trino.auth.JWTAuthentication(caller.token))
        if o.user is None:
            raise IdentityError("this Trino connection has no service user; pass a caller with a token")
        auth = trino.auth.BasicAuthentication(o.user, o.password.get_secret_value()) if o.password else None
        run_as = caller.username if caller is not None and self.identity_mode is IdentityMode.IMPERSONATE else o.user
        return trino.dbapi.connect(**common, user=run_as, auth=auth)

    def query(self, sql: str, caller: Caller | None = None, *, limit: int | None = 1000) -> QueryResult:
        connection = self._connect(caller)
        try:
            cursor = connection.cursor()
            if caller is not None and self.identity_mode is IdentityMode.IMPERSONATE:
                cursor.execute("SELECT current_user")
                effective = cursor.fetchone()
                if not effective or effective[0] != caller.username:
                    raise TrinoIdentityError("Trino did not run the session as the impersonated caller")
            cursor.execute(sql)
            if cursor.description is None:
                return QueryResult([], [])
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(limit) if limit else cursor.fetchall()
            return QueryResult(columns, [tuple(r) for r in rows])
        finally:
            connection.close()

    def ping(self) -> bool:
        return self.query("SELECT 1").rows == [(1,)]

    def row_count(self, database: str, table: str, *, caller: Caller | None = None) -> int:
        """The connector's statistics when it has them (SHOW STATS); COUNT(*) otherwise."""
        try:
            stats = self.query(f"SHOW STATS FOR {self.dialect.render(self.dialect.table(f'{database}.{table}'))}", caller, limit=None)
            summary = [r for r in stats.rows if r[0] is None]  # the row without a column name
            count = summary[0][stats.columns.index("row_count")] if summary else None
            if count is not None:
                return int(count)
        except Exception:  # noqa: BLE001 - statistics are optional
            pass
        return super().row_count(database, table, caller=caller)

    def recent_queries(self, limit: int, *, caller: Caller | None = None) -> tuple[str, list[str]]:
        """Finished queries the coordinator still remembers (system.runtime.queries)."""
        q = (exp.select("query").from_("system.runtime.queries")
             .where(exp.column("state").eq(exp.Literal.string("FINISHED")))
             .order_by(exp.Ordered(this=exp.column("created"), desc=True)).limit(limit))
        return "system.runtime.queries", [r[0] for r in self.query(self.dialect.render(q), caller, limit=limit).rows if r[0]]
