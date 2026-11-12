"""SqlDialect: how Helios writes SQL for an engine (burndown 3.6)."""
import pytest
from sqlglot import exp

from helios_cortex.engines import SqlDialect, dialect_for
from helios_cortex.engines.duckdb_engine import DuckDBEngine


def rendered(dialect, expression):
    return SqlDialect(dialect).render(exp.select(expression).from_("t"))


@pytest.mark.parametrize(("dialect", "approximate"), [
    ("duckdb", "APPROX_COUNT_DISTINCT(x)"),
    ("snowflake", "APPROX_COUNT_DISTINCT(x)"),
    ("bigquery", "APPROX_COUNT_DISTINCT(x)"),
    ("trino", "APPROX_DISTINCT(x)"),
    ("postgres", "COUNT(DISTINCT x)"),   # no approximate count: exact
    ("sqlite", "COUNT(DISTINCT x)"),
    ("mysql", "COUNT(DISTINCT x)"),
    ("tsql", "COUNT(DISTINCT x)"),       # SQLGlot renders a function SQL Server does not have
])
def test_approximate_counts_only_where_the_database_has_them(dialect, approximate):
    d = SqlDialect(dialect)
    assert rendered(dialect, d.count_distinct(exp.column("x"), approximate=True)) == f"SELECT {approximate} FROM t"
    assert rendered(dialect, d.count_distinct(exp.column("x"))) == "SELECT COUNT(DISTINCT x) FROM t"


def test_catalog_names_are_quoted_so_reserved_words_and_case_survive():
    d = SqlDialect("postgres")
    sql = d.render(exp.select(d.column("order"), d.column("Region"), d.column("id", table="o")).from_(d.table("sales.orders")))
    assert sql == 'SELECT "order", "Region", "o"."id" FROM "sales"."orders"'
    assert SqlDialect("bigquery").render(exp.select(d.column("a b")).from_(d.table("t"))) == "SELECT `a b` FROM `t`"


def test_an_unknown_dialect_fails_when_it_is_named():
    with pytest.raises(ValueError, match="Unknown dialect 'postgresq'"):
        SqlDialect("postgresq")


def test_a_name_or_a_dialect_is_accepted_where_sql_is_rendered():
    d = SqlDialect("duckdb")
    assert dialect_for(d) is d
    assert (dialect_for("trino").name, dialect_for("trino").approx_distinct) == ("trino", True)
    assert dialect_for("postgres").approx_distinct is False
    assert SqlDialect("postgres", approx_distinct=True).approx_distinct is True


def test_compiled_queries_are_pretty_and_statements_are_not():
    d = SqlDialect("duckdb")
    q = exp.select("a", "b").from_("t")
    assert d.render(q) == "SELECT a, b FROM t"
    assert d.render_query(q) == "SELECT\n  a,\n  b\nFROM t"


def test_engines_supply_their_dialect():
    assert DuckDBEngine().dialect.name == "duckdb"


def test_a_dialect_is_named_by_sqlglot_or_by_an_engine_plugin():
    """dialect_named (burndown 6.2): a SQLGlot name, or an installed engine plugin's own
    dialect when it declares one; anything else is refused."""
    from helios_cortex import plugins
    from helios_cortex.engines import SqlDialect, dialect_named

    assert type(dialect_named("duckdb")) is SqlDialect and dialect_named("duckdb").name == "duckdb"
    if "impala" in plugins.names(plugins.ENGINES):
        assert type(dialect_named("impala")).__name__ == "ImpalaDialect"
    with pytest.raises(ValueError, match="unknown SQL dialect 'cobol'"):
        dialect_named("cobol")
    # an engine plugin whose client is missing (trino without its extra) still names SQLGlot's dialect
    assert dialect_named("trino").name == "trino"


def test_engines_raise_the_neutral_identity_errors():
    from helios_cortex import EngineAuthenticationError, ImpersonationError
    from helios_cortex.engines.duckdb_engine import DuckDBEngine

    assert issubclass(EngineAuthenticationError, PermissionError) and issubclass(ImpersonationError, PermissionError)
    assert DuckDBEngine().rejected_value_type(RuntimeError("are not comparable")) is False   # the default
