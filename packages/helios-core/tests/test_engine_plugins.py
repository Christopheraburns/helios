"""Engines are plugins built from settings: duckdb and sqlalchemy ship with core (burndown 3.5)."""
from importlib.metadata import EntryPoint
from importlib.util import find_spec

import pytest
from pydantic import ValidationError

from helios_core import Caller, plugins
from helios_core.compiler import Compiler, SemanticRequest
from helios_core.engines import Engine, engine_for
from helios_core.engines.duckdb_engine import DuckDBEngine
from helios_core.ossie import SemanticModel
from helios_core.settings import Settings

needs_sqlalchemy = pytest.mark.skipif(find_spec("sqlalchemy") is None, reason="needs helios-core[sqlalchemy]")


def connection(**entry):
    return Settings(connections={"c": entry}).connection("c")


def test_duckdb_is_built_from_its_path(tmp_path):
    path = tmp_path / "local.duckdb"
    engine = engine_for(connection(engine="duckdb", path=str(path)))
    assert isinstance(engine, DuckDBEngine)
    engine.query("CREATE TABLE t AS SELECT 42 AS answer")
    engine.con.close()
    assert engine_for(connection(engine="duckdb", path=str(path))).query("SELECT answer FROM t").rows == [(42,)]
    assert engine_for(connection(engine="duckdb")).query("SELECT 1").rows == [(1,)]  # :memory: by default


def test_a_plugin_rejects_options_it_does_not_know_and_modes_it_cannot_honour():
    with pytest.raises(ValidationError, match="database"):
        engine_for(connection(engine="duckdb", database="x"))
    with pytest.raises(ValueError, match="does not support identity mode 'impersonate'"):
        engine_for(connection(engine="duckdb", identity="impersonate"))


@needs_sqlalchemy
def test_sqlalchemy_needs_a_url():
    with pytest.raises(ValidationError, match="url"):
        engine_for(connection(engine="sqlalchemy"))


@pytest.fixture
def sales(tmp_path):
    engine = engine_for(connection(engine="sqlalchemy", url=f"sqlite:///{tmp_path / 'sales.db'}"))
    engine.query("CREATE TABLE orders (id INTEGER PRIMARY KEY, region TEXT NOT NULL, amount REAL)")
    engine.query("CREATE VIEW big_orders AS SELECT * FROM orders WHERE amount > 100")
    engine.query("INSERT INTO orders (region, amount) VALUES ('west', 120.5), ('east', 80), ('west', 10)")
    return engine


@needs_sqlalchemy
def test_sqlalchemy_runs_sql_on_any_dialect_it_knows(sales):
    from helios_core.engines.sqlalchemy_engine import SqlAlchemyEngine

    assert isinstance(sales, SqlAlchemyEngine)
    assert (sales.backend, sales.sqlglot_dialect) == ("sqlite", "sqlite")
    assert sales.ping()
    result = sales.query("SELECT region, SUM(amount) AS total FROM orders GROUP BY region ORDER BY region")
    assert (result.columns, result.rows) == (["region", "total"], [("east", 80.0), ("west", 130.5)])
    assert len(sales.query("SELECT * FROM orders", limit=2).rows) == 2
    # ':' and '%' reach the database untouched instead of being read as placeholders.
    assert sales.query("SELECT '12:30', 'a%b' LIKE 'a%'").rows == [("12:30", 1)]


@needs_sqlalchemy
def test_sqlalchemy_introspects_through_the_inspector(sales):
    assert sales.databases() == ["main"]
    assert sales.tables("main") == ["big_orders", "orders"]
    assert sales.columns("main", "orders") == [
        {"name": "id", "type": "INTEGER", "comment": ""},
        {"name": "region", "type": "TEXT", "comment": ""},
        {"name": "amount", "type": "REAL", "comment": ""},
    ]


@needs_sqlalchemy
def test_sqlalchemy_is_service_only_and_never_shows_its_url():
    from helios_core.engines.sqlalchemy_engine import SqlAlchemyEngine

    with pytest.raises(ValueError, match="does not support identity mode 'token'"):
        engine_for(connection(engine="sqlalchemy", url="sqlite://", identity="token"))
    settings = Settings(connections={"pg": {"engine": "sqlalchemy", "url": "postgresql://helios:hunter2@db/sales"}})
    assert "hunter2" not in repr(settings)
    engine = SqlAlchemyEngine("sqlite://")
    assert engine.query("SELECT 7", Caller("alice", token="t")).rows == [(7,)]  # service: the caller is not used


@needs_sqlalchemy
def test_the_compiler_targets_the_engines_dialect(sales):
    def ansi(expression):
        return {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}

    model = SemanticModel({
        "name": "sales",
        "datasets": [{
            "name": "orders", "source": "main.orders", "primary_key": ["id"],
            "fields": [{"name": "region", "expression": ansi("region"), "datatype": "String", "dimension": {"is_time": False}}],
        }],
        "relationships": [],
        "metrics": [{"name": "revenue", "expression": ansi("SUM(orders.amount)"), "ai_context": {"synonyms": ["Revenue"]}}],
    })
    request = SemanticRequest(metrics=["Revenue"], dimensions=["region"])
    sql = Compiler(model).compile(request, dialect=sales.sqlglot_dialect).sql
    assert sorted(sales.query(sql).rows) == [("east", 80.0), ("west", 130.5)]


def test_engine_for_needs_an_engine_class(monkeypatch):
    entries = [EntryPoint("broken", "helios_core.settings:Settings", plugins.ENGINES)]
    monkeypatch.setattr(plugins, "_entry_points", lambda group: tuple(e for e in entries if e.group == group))
    with pytest.raises(plugins.PluginError, match="not an Engine subclass"):
        engine_for(connection(engine="broken"))
    with pytest.raises(plugins.PluginNotFound, match="no plugin 'duckdb'"):
        engine_for(connection(engine="duckdb"))


def test_an_engine_that_cannot_be_configured_says_so():
    class Bare(Engine):
        name = "bare"

        def query(self, sql, caller=None, *, limit=1000):
            raise NotImplementedError

        def ping(self):
            return False

    with pytest.raises(NotImplementedError, match="the bare engine cannot be built from settings"):
        Bare.from_connection(connection(engine="bare"))
