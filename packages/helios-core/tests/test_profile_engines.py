"""Harvest and profile run on any engine (burndown 3.6): the same star schema on DuckDB
and on SQLite through the SQLAlchemy engine gives the same evidence, and on SQLite a
compiled metric then runs too."""
from importlib.util import find_spec

import pytest

from helios_core.catalog import Harvester
from helios_core.compiler import Compiler, SemanticRequest
from helios_core.engines import engine_for
from helios_core.ossie import SemanticModel
from helios_core.profiler import Profiler
from helios_core.settings import Settings

TABLES = [
    "CREATE TABLE {s}customers (c_customer_sk INTEGER, c_name VARCHAR(40), c_region VARCHAR(10))",
    "CREATE TABLE {s}orders (o_order_sk INTEGER, o_customer_sk INTEGER, o_amount DOUBLE PRECISION, \"order\" INTEGER)",
]
ROWS = [
    "INSERT INTO {s}customers VALUES " + ", ".join(
        f"({i}, 'customer {i}', '{'west' if i % 2 else 'east'}')" for i in range(1, 21)),
    "INSERT INTO {s}orders VALUES " + ", ".join(
        f"({i}, {1 + i % 20}, {i * 1.5}, {i % 3})" for i in range(1, 61)),
]


def load(engine, schema):
    prefix = f"{schema}." if schema != "main" else ""
    if prefix:
        engine.query(f"CREATE SCHEMA {schema}")
    for statement in TABLES + ROWS:
        engine.query(statement.format(s=prefix))
    return engine


def duckdb_engine(tmp_path):
    return load(engine_for(Settings(connections={"d": {"engine": "duckdb"}}).connection("d")), "sales"), "sales"


def sqlite_engine(tmp_path):
    if find_spec("sqlalchemy") is None:
        pytest.skip("needs helios-core[sqlalchemy]")
    url = f"sqlite:///{tmp_path / 'sales.db'}"
    return load(engine_for(Settings(connections={"s": {"engine": "sqlalchemy", "url": url}}).connection("s")), "main"), "main"


def evidence(engine, schema):
    harvest = Harvester(engine).run([schema])
    profile = Profiler(engine).run(harvest)
    return harvest, profile


@pytest.mark.parametrize("make", [duckdb_engine, sqlite_engine], ids=["duckdb", "sqlalchemy-sqlite"])
def test_harvest_and_profile_find_the_keys_and_the_join(make, tmp_path):
    engine, schema = make(tmp_path)
    harvest, profile = evidence(engine, schema)

    tables = {t["table"]: t for t in harvest["tables"]}
    assert {name: t["row_count"] for name, t in tables.items()} == {"customers": 20, "orders": 60}
    assert [c["name"] for c in tables["orders"]["columns"]] == ["o_order_sk", "o_customer_sk", "o_amount", "order"]
    assert harvest["queries"]["sources"] == [f"no query log for the {engine.name} engine"]

    profiles = profile["tables"]
    orders = profiles[f"{schema}.orders"]
    assert orders["primary_keys"] == [{"column": "o_order_sk", "confidence": 0.95}]
    assert orders["stats"]["columns"]["order"]["min"] == 0          # a reserved-word column profiles too
    assert profiles[f"{schema}.customers"]["stats"]["columns"]["c_region"]["ndv"] == 2
    [join] = profile["relationships"]
    assert (join["from"], join["from_column"], join["to"], join["to_column"], join["match_ratio"]) == (
        f"{schema}.orders", "o_customer_sk", f"{schema}.customers", "c_customer_sk", 1.0)


def test_a_compiled_metric_runs_on_the_sqlalchemy_engine(tmp_path):
    engine, schema = sqlite_engine(tmp_path)

    def ansi(expression):
        return {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}

    model = SemanticModel({
        "name": "sales",
        "datasets": [
            {"name": "orders", "source": "main.orders", "primary_key": ["o_order_sk"], "fields": []},
            {"name": "customers", "source": "main.customers", "primary_key": ["c_customer_sk"],
             "fields": [{"name": "c_region", "expression": ansi("c_region"), "datatype": "String",
                         "dimension": {"is_time": False}}]},
        ],
        "relationships": [{"name": "orders__o_customer_sk__customers", "from": "orders", "to": "customers",
                           "from_columns": ["o_customer_sk"], "to_columns": ["c_customer_sk"]}],
        "metrics": [{"name": "revenue", "expression": ansi("SUM(orders.o_amount)"), "ai_context": {"synonyms": ["Revenue"]}}],
    })
    sql = Compiler(model).compile(SemanticRequest(metrics=["Revenue"], dimensions=["c_region"]), dialect=engine.dialect).sql
    totals = dict(engine.query(sql).rows)
    assert totals == {"east": pytest.approx(1350.0), "west": pytest.approx(1395.0)}
