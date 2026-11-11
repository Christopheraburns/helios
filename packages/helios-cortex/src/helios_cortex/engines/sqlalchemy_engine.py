"""SQLAlchemy engine: any database SQLAlchemy has a dialect for (Postgres, Snowflake,
BigQuery, Trino, Databricks, MySQL, SQLite, ...), named by a SQLAlchemy URL.

    [connections.sales]
    engine = "sqlalchemy"
    url = "postgresql+psycopg://helios@db.example.com/sales"

Install the database's own driver (``helios-cortex[postgres]`` brings Postgres's). The URL's
credentials are the connection's service identity, so ``service`` is the only
identity mode. Catalog introspection goes through the SQLAlchemy inspector.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from ..caller import Caller, IdentityMode
from .base import Engine, QueryResult
from .dialect import SqlDialect

import sqlalchemy

if TYPE_CHECKING:
    from ..settings import ConnectionSettings

# SQLAlchemy backend name -> SQLGlot dialect, where they differ.
SQLGLOT_DIALECTS = {"postgresql": "postgres", "mssql": "tsql", "awsathena": "athena"}


def _sqlglot_name(backend: str) -> str:
    """The SQLGlot dialect for a SQLAlchemy backend; SQLGlot's generic SQL when it has none."""
    name = SQLGLOT_DIALECTS.get(backend, backend)
    try:
        Dialect.get_or_raise(name)
    except ValueError:
        return ""
    return name


class SqlAlchemyDialect(SqlDialect):
    """Names come from the SQLAlchemy inspector, which lower-cases case-insensitive names
    on databases that store them upper-case (Snowflake, Oracle); they are turned back
    into the stored name before quoting."""

    def __init__(self, name: str, sqlalchemy_dialect: sqlalchemy.Dialect):
        super().__init__(name)
        self._sqlalchemy = sqlalchemy_dialect

    def identifier(self, name: str) -> exp.Identifier:
        if self._sqlalchemy.requires_name_normalize:
            name = str(self._sqlalchemy.denormalize_name(name) or name)
        return super().identifier(name)


class SqlAlchemyOptions(BaseModel):
    """Options of an ``engine = "sqlalchemy"`` connection."""

    model_config = ConfigDict(extra="forbid")

    url: SecretStr  # may embed a password; Settings keeps it hidden


class SqlAlchemyEngine(Engine):
    name = "sqlalchemy"

    def __init__(self, url: str, identity_mode: IdentityMode | str = IdentityMode.SERVICE):
        super().__init__(identity_mode)
        self._engine = sqlalchemy.create_engine(url)
        self.backend = self._engine.dialect.name
        self.sqlglot_dialect = _sqlglot_name(self.backend)

    @property
    def dialect(self) -> SqlAlchemyDialect:
        return SqlAlchemyDialect(self.sqlglot_dialect, self._engine.dialect)

    @classmethod
    def from_connection(cls, connection: ConnectionSettings) -> SqlAlchemyEngine:
        options = SqlAlchemyOptions.model_validate(connection.options)
        return cls(options.url.get_secret_value(), connection.identity)

    def query(self, sql: str, caller: Caller | None = None, *, limit: int | None = 1000) -> QueryResult:
        # The DB-API cursor, without parameters: compiled SQL may contain ':' and '%',
        # which text() and pyformat drivers would read as placeholders.
        self.check_caller(caller)
        connection = self._engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            if cursor.description is None:
                result = QueryResult([], [])
            else:
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchmany(limit) if limit else cursor.fetchall()
                result = QueryResult(columns, [tuple(r) for r in rows])
            connection.commit()
            return result
        finally:
            connection.close()

    def ping(self) -> bool:
        return self.query("SELECT 1").rows == [(1,)]

    def _inspector(self, caller: Caller | None) -> sqlalchemy.Inspector:
        self.check_caller(caller)  # service mode only, so the inspector's connection is the caller's view
        return sqlalchemy.inspect(self._engine)  # a new one each time: the inspector caches

    def databases(self, *, caller: Caller | None = None) -> list[str]:
        return self._inspector(caller).get_schema_names()

    def tables(self, database: str, *, caller: Caller | None = None) -> list[str]:
        inspector = self._inspector(caller)
        return sorted(inspector.get_table_names(schema=database) + inspector.get_view_names(schema=database))

    def columns(self, database: str, table: str, *, caller: Caller | None = None) -> list[dict]:
        return [
            {"name": column["name"], "type": str(column["type"]), "comment": column.get("comment") or ""}
            for column in self._inspector(caller).get_columns(table, schema=database)
        ]
