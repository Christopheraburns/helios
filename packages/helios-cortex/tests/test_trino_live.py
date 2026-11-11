"""The Trino engine against a real Trino server (burndown 3.8).

Runs when HELIOS_TEST_TRINO names an open (unauthenticated, http) Trino coordinator
with the memory catalog, e.g. the official image:

    docker run -d -p 127.0.0.1:28080:8080 trinodb/trino:484
    HELIOS_TEST_TRINO=127.0.0.1:28080 uv run pytest packages/helios-core/tests/test_trino_live.py

CI starts that container (the trino job in .github/workflows/helios.yml). Token mode
needs a cluster configured for JWT and is covered by test_trino_engine.py.
"""
import os
import uuid

import pytest

from helios_core import Caller
from helios_core.catalog import Harvester
from helios_core.compiler import Compiler, SemanticRequest
from helios_core.engines import engine_for
from helios_core.ossie import SemanticModel
from helios_core.profiler import Profiler
from helios_core.settings import Settings

SERVER = os.environ.get("HELIOS_TEST_TRINO")
pytestmark = pytest.mark.skipif(not SERVER, reason="set HELIOS_TEST_TRINO=host:port to run against Trino")


def lake(identity="service"):
    host, _, port = (SERVER or "").partition(":")
    return engine_for(Settings(connections={"lake": {
        "engine": "trino", "host": host, "port": int(port or 8080), "catalog": "memory",
        "user": "helios", "http_scheme": "http", "identity": identity}}).connection("lake"))


@pytest.fixture(scope="module")
def schema():
    engine, name = lake(), f"helios_{uuid.uuid4().hex[:8]}"
    engine.query(f"CREATE SCHEMA memory.{name}")
    engine.query(f"CREATE TABLE memory.{name}.customers (c_customer_sk INTEGER, c_name VARCHAR, c_region VARCHAR)")
    engine.query(f'CREATE TABLE memory.{name}.orders (o_order_sk INTEGER, o_customer_sk INTEGER, o_amount DOUBLE, "order" INTEGER)')
    engine.query(f"INSERT INTO memory.{name}.customers VALUES " + ", ".join(
        f"({i}, 'customer {i}', '{'west' if i % 2 else 'east'}')" for i in range(1, 21)))
    engine.query(f"INSERT INTO memory.{name}.orders VALUES " + ", ".join(
        f"({i}, {1 + i % 20}, {i * 1.5}, {i % 3})" for i in range(1, 61)))
    yield name
    for table in ("orders", "customers"):
        engine.query(f"DROP TABLE memory.{name}.{table}")
    engine.query(f"DROP SCHEMA memory.{name}")


def test_harvest_and_profile_run_on_trino(schema):
    engine = lake()
    assert schema in engine.databases()
    harvest = Harvester(engine).run([schema])
    tables = {t["table"]: t for t in harvest["tables"]}
    assert {name: t["row_count"] for name, t in tables.items()} == {"customers": 20, "orders": 60}
    assert [c["name"] for c in tables["orders"]["columns"]] == ["o_order_sk", "o_customer_sk", "o_amount", "order"]
    assert harvest["queries"]["sources"] == ["system.runtime.queries"]

    profile = Profiler(engine).run(harvest)
    orders = profile["tables"][f"{schema}.orders"]
    assert orders["primary_keys"] == [{"column": "o_order_sk", "confidence": 0.95}]
    assert orders["stats"]["columns"]["order"]["max"] == 2
    [join] = profile["relationships"]
    assert (join["from_column"], join["to"], join["to_column"], join["match_ratio"]) == (
        "o_customer_sk", f"{schema}.customers", "c_customer_sk", 1.0)


def test_a_compiled_metric_runs_on_trino_as_the_impersonated_caller(schema):
    def ansi(expression):
        return {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}

    model = SemanticModel({
        "name": "sales",
        "datasets": [
            {"name": "orders", "source": f"{schema}.orders", "primary_key": ["o_order_sk"], "fields": []},
            {"name": "customers", "source": f"{schema}.customers", "primary_key": ["c_customer_sk"],
             "fields": [{"name": "c_region", "expression": ansi("c_region"), "datatype": "String",
                         "dimension": {"is_time": False}}]},
        ],
        "relationships": [{"name": "orders__o_customer_sk__customers", "from": "orders", "to": "customers",
                           "from_columns": ["o_customer_sk"], "to_columns": ["c_customer_sk"]}],
        "metrics": [{"name": "revenue", "expression": ansi("SUM(orders.o_amount)"), "ai_context": {"synonyms": ["Revenue"]}}],
    })
    engine = lake("impersonate")
    sql = Compiler(model).compile(SemanticRequest(metrics=["Revenue"], dimensions=["c_region"]), dialect=engine.dialect).sql
    totals = dict(engine.query(sql, Caller("alice")).rows)
    assert totals == {"east": pytest.approx(1350.0), "west": pytest.approx(1395.0)}
    assert engine.query("SELECT current_user", Caller("alice")).rows == [("alice",)]
    assert engine.query("SELECT current_user").rows == [("helios",)]   # Helios's own work: the service user
