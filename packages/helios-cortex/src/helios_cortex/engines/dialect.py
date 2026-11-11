"""How Helios writes SQL for one engine: a SQLGlot dialect plus the engine's own rules.

Helios builds statements as SQLGlot expressions (the compiler's queries, the
profiler's statistics, catalog lookups) and an engine's ``dialect`` renders them.
An engine plugin returns a SqlDialect, or a subclass for what SQLGlot does not
know about its database (helios-cloudera's ImpalaDialect).
"""
from __future__ import annotations

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

# SQLGlot dialects whose rendering of an approximate distinct count the database
# accepts. Elsewhere (Postgres, SQLite, MySQL, Oracle, SQL Server, ...) Helios counts
# exactly, which is slower but correct.
APPROX_DISTINCT_DIALECTS = frozenset({
    "athena", "bigquery", "clickhouse", "databricks", "duckdb", "presto",
    "redshift", "snowflake", "spark", "trino",
})


class SqlDialect:
    def __init__(self, name: str, *, approx_distinct: bool | None = None):
        Dialect.get_or_raise(name)  # an unknown name fails here, not at the first query
        self.name = name
        self.approx_distinct = name in APPROX_DISTINCT_DIALECTS if approx_distinct is None else approx_distinct

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    # ------------------------------------------------------------ building
    def identifier(self, name: str) -> exp.Identifier:
        """A name as the catalog reports it, quoted so reserved words and case survive."""
        return exp.Identifier(this=name, quoted=True)

    def column(self, name: str, table: str | None = None) -> exp.Column:
        return exp.Column(this=self.identifier(name), table=self.identifier(table) if table else None)

    def table(self, qualified: str) -> exp.Table:
        """``schema.table`` (or ``table``) from the catalog."""
        *schema, name = qualified.split(".")
        return exp.Table(this=self.identifier(name), db=self.identifier(".".join(schema)) if schema else None)

    def count_distinct(self, expression: exp.Expression, *, approximate: bool = False) -> exp.Expression:
        """COUNT(DISTINCT x), or the engine's approximate count when it has one and it is wanted."""
        if approximate and self.approx_distinct:
            return exp.ApproxDistinct(this=expression)
        return exp.Count(this=exp.Distinct(expressions=[expression]))

    # ------------------------------------------------------------ rendering
    def render(self, expression: exp.Expression) -> str:
        """A statement Helios runs for itself (profiling, catalog lookups)."""
        return expression.sql(dialect=self.name)

    def render_query(self, expression: exp.Expression) -> str:
        """A compiled semantic query, which a person may read: pretty-printed."""
        return expression.sql(dialect=self.name, pretty=True)


def dialect_for(dialect: SqlDialect | str) -> SqlDialect:
    """``dialect`` as a SqlDialect; a string names a SQLGlot dialect with no engine rules."""
    return dialect if isinstance(dialect, SqlDialect) else SqlDialect(dialect)
