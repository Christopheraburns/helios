"""A compiled metric runs on the SQLAlchemy engine (burndown 3.6), on the star schema
helios-model's test_discovery_engines.py harvests and profiles."""
import pytest

from helios_cortex.compiler import Compiler, SemanticRequest
from helios_cortex.engines import engine_for
from helios_cortex.ossie import SemanticModel
from helios_cortex.settings import Settings

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


def sqlite_engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'sales.db'}"
    return load(engine_for(Settings(connections={"s": {"engine": "sqlalchemy", "url": url}}).connection("s")), "main"), "main"


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
