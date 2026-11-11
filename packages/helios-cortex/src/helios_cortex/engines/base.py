"""Engine adapter interface. Every SQL engine helios talks to implements this.

An engine plugin (entry-point group ``helios.engines``) is an Engine subclass whose
``from_connection`` builds it from a ``[connections.<name>]`` entry; ``engine_for``
loads the plugin a connection names and calls it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sqlglot import exp

from ..caller import Caller, IdentityError, IdentityMode
from .dialect import SqlDialect

if TYPE_CHECKING:
    from ..settings import ConnectionSettings


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]

    def dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "sys"})


class Engine(ABC):
    """A SQL engine. ``query(sql)`` with no caller runs as the connection's own
    (service) identity, for Helios's own work: harvesting, profiling, its index.
    ``query(sql, caller)`` runs a caller's query under ``identity_mode``, which the
    host picks per connection from the modes the engine supports."""

    name: str = "engine"
    sqlglot_dialect: str = ""
    identity_modes: ClassVar[frozenset[IdentityMode]] = frozenset({IdentityMode.SERVICE})

    def __init__(self, identity_mode: IdentityMode | str = IdentityMode.SERVICE):
        mode = IdentityMode(identity_mode)
        if mode not in self.identity_modes:
            supported = ", ".join(sorted(m.value for m in self.identity_modes))
            raise ValueError(f"the {self.name} engine does not support identity mode {mode.value!r} (supported: {supported})")
        self.identity_mode = mode

    @classmethod
    def from_connection(cls, connection: ConnectionSettings) -> Engine:
        """The engine for a ``[connections.<name>]`` entry. The plugin validates
        ``connection.options`` and must honour ``connection.identity``."""
        raise NotImplementedError(f"the {cls.name} engine cannot be built from settings")

    def check_caller(self, caller: Caller | None) -> None:
        """Raise IdentityError when ``caller`` lacks what ``identity_mode`` needs."""
        if caller is None or self.identity_mode is IdentityMode.SERVICE:
            return
        if self.identity_mode is IdentityMode.IMPERSONATE and caller.username is None:
            raise IdentityError("impersonate mode needs the caller's username")
        if self.identity_mode is IdentityMode.TOKEN and caller.token is None:
            raise IdentityError("token mode needs the caller's token")

    @abstractmethod
    def query(self, sql: str, caller: Caller | None = None, *, limit: int | None = 1000) -> QueryResult: ...

    @abstractmethod
    def ping(self) -> bool: ...

    @property
    def dialect(self) -> SqlDialect:
        """How SQL is written for this engine. A plugin overrides it for rules SQLGlot lacks."""
        return SqlDialect(self.sqlglot_dialect)

    # Catalog lookups and statistics for Helios's own work (harvesting), as the service
    # identity. The defaults read information_schema, which most SQL databases have; an
    # engine with another catalog overrides them (Impala's SHOW statements, SQLAlchemy's
    # inspector).
    def databases(self) -> list[str]:
        q = exp.select("schema_name").distinct().from_("information_schema.schemata").order_by("schema_name")
        return [r[0] for r in self.query(self.dialect.render(q), limit=None).rows if r[0].lower() not in SYSTEM_SCHEMAS]

    def tables(self, database: str) -> list[str]:
        q = (exp.select("table_name").from_("information_schema.tables")
             .where(exp.column("table_schema").eq(exp.Literal.string(database))).order_by("table_name"))
        return [r[0] for r in self.query(self.dialect.render(q), limit=None).rows]

    def columns(self, database: str, table: str) -> list[dict]:
        q = (exp.select("column_name", "data_type").from_("information_schema.columns")
             .where(exp.and_(exp.column("table_schema").eq(exp.Literal.string(database)),
                             exp.column("table_name").eq(exp.Literal.string(table))))
             .order_by("ordinal_position"))
        return [{"name": r[0], "type": r[1], "comment": ""} for r in self.query(self.dialect.render(q), limit=None).rows]

    def row_count(self, database: str, table: str) -> int:
        """Rows in the table. An engine with cheap table statistics overrides this."""
        q = exp.select(exp.Count(this=exp.Star())).from_(self.dialect.table(f"{database}.{table}"))
        return int(self.query(self.dialect.render(q)).rows[0][0])

    def recent_queries(self, limit: int) -> tuple[str, list[str]] | None:
        """Recent statements from the engine's query log, newest first, with the log's
        name; None when the engine keeps no log Helios can read."""
        return None
