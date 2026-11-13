"""DuckDB engine: the built-in plugin for local work and tests. It runs the same harvest,
profile and compiled queries as a warehouse, rendered in DuckDB's dialect."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..caller import Caller, IdentityMode
from .base import Engine, QueryResult

if TYPE_CHECKING:
    from ..settings import ConnectionSettings


class DuckDBOptions(BaseModel):
    """Options of an ``engine = "duckdb"`` connection. ``path`` is a database file,
    or ``:memory:`` (the default) for an empty in-process database. A file is opened
    read-only unless ``read_only = false``: Helios only reads the warehouse, and DuckDB
    lets many processes share a file read-only (the facade in a notebook, ``helios mcp``
    for an agent, a harvest) but none beside a writer. It also means a mistyped path is
    an error rather than a new, empty database."""

    model_config = ConfigDict(extra="forbid")

    path: str = ":memory:"
    read_only: bool = True


class DuckDBEngine(Engine):
    """An embedded database has no users, so every query runs as the process: ``service`` only."""

    name = "duckdb"
    sqlglot_dialect = "duckdb"

    def __init__(self, path: str = ":memory:", identity_mode: IdentityMode | str = IdentityMode.SERVICE, *,
                 read_only: bool = False):
        super().__init__(identity_mode)
        import duckdb
        self.con = duckdb.connect(path, read_only=read_only and path != ":memory:")

    @classmethod
    def from_connection(cls, connection: ConnectionSettings) -> DuckDBEngine:
        """The engine for a connection: its file read-only unless ``read_only = false``."""
        options = DuckDBOptions.model_validate(connection.options)
        return cls(options.path, connection.identity, read_only=options.read_only)

    def databases(self, *, caller: Caller | None = None) -> list[str]:
        # information_schema lists every attached catalog's schemas (system, temp); keep this database's.
        rows = self.query(
            "SELECT DISTINCT schema_name FROM information_schema.schemata "
            "WHERE catalog_name = current_database() ORDER BY 1", caller, limit=None).rows
        return [r[0] for r in rows if r[0] not in ("information_schema", "pg_catalog")]

    def query(self, sql: str, caller: Caller | None = None, *, limit: int | None = 1000) -> QueryResult:
        cur = self.con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit) if limit else cur.fetchall()
        return QueryResult(cols, [tuple(r) for r in rows])

    def ping(self) -> bool:
        return self.query("SELECT 1").rows == [(1,)]
