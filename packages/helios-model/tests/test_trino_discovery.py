"""Harvest and profile on a real Trino server (burndown 3.8).

Runs when HELIOS_TEST_TRINO names an open (unauthenticated, http) Trino coordinator
with the memory catalog, e.g. the official image:

    docker run -d -p 127.0.0.1:28080:8080 trinodb/trino:484
    HELIOS_TEST_TRINO=127.0.0.1:28080 uv run pytest packages/helios-model/tests/test_trino_discovery.py

CI starts that container (the trino job in .github/workflows/helios.yml). The engine
itself (compiled queries, identity modes) is tested in helios-cortex's test_trino_live.py.
"""
import os
import uuid

import pytest

from helios_model.harvest import TableHarvester
from helios_cortex.engines import engine_for
from helios_model.profile import TableProfiler
from helios_cortex.settings import Settings

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
    harvest = TableHarvester(engine).run([schema])
    tables = {t["table"]: t for t in harvest["tables"]}
    assert {name: t["row_count"] for name, t in tables.items()} == {"customers": 20, "orders": 60}
    assert [c["name"] for c in tables["orders"]["columns"]] == ["o_order_sk", "o_customer_sk", "o_amount", "order"]
    assert harvest["queries"]["sources"] == ["system.runtime.queries"]

    profile = TableProfiler(engine).run(harvest)
    orders = profile["tables"][f"{schema}.orders"]
    assert orders["primary_keys"] == [{"column": "o_order_sk", "confidence": 0.95}]
    assert orders["stats"]["columns"]["order"]["max"] == 2
    [join] = profile["relationships"]
    assert (join["from_column"], join["to"], join["to_column"], join["match_ratio"]) == (
        "o_customer_sk", f"{schema}.customers", "c_customer_sk", 1.0)
