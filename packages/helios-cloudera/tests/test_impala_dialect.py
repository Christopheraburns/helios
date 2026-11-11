"""Impala's dialect hooks and catalog statements (burndown 3.6).

The golden files are the sample requests compiled for Impala; they are what
helios_cortex rendered before the hooks existed (Hive, without backticks). Regenerate
with HELIOS_UPDATE_GOLDEN=1, as for helios-cortex's golden files.
"""
import json
import os
from pathlib import Path

import pytest
import sqlglot

from helios_cortex.compiler import Compiler, SemanticRequest
from helios_cortex.ossie import SemanticModel
from helios_model.profile import TableProfiler
from helios_cortex.engines import QueryResult

from helios_cloudera.impala import IMPALA, ImpalaConfig, ImpalaEngine

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
GOLDEN = HERE / "golden" / "compile" / "impala"
REQUESTS = json.loads((ROOT / "packages" / "helios-cortex" / "tests" / "fixtures" / "sample_requests.json").read_text())
UPDATE = os.environ.get("HELIOS_UPDATE_GOLDEN") == "1"


@pytest.fixture(scope="module")
def model():
    return SemanticModel.load(str(ROOT / "models" / "published" / "tpcds.ossie.yaml"))


@pytest.mark.parametrize("case", sorted(REQUESTS))
def test_compiled_impala_sql_matches_the_golden_file(model, case):
    request = SemanticRequest.from_dict(REQUESTS[case])
    sql = Compiler(model).compile(request, dialect=IMPALA).sql + "\n"
    # What helios_cortex did before the hooks: render Hive, then drop the backticks.
    assert sql == Compiler(model).compile(request, dialect="hive").sql.replace("`", "") + "\n"
    sqlglot.parse_one(sql, read="hive")
    path = GOLDEN / f"{case}.sql"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql)
    assert path.read_text() == sql, f"{path.relative_to(HERE)} is stale; see this module's docstring"


def test_every_impala_golden_file_has_a_request():
    assert {p.stem for p in GOLDEN.glob("*.sql")} == set(REQUESTS)


class Recording(ImpalaEngine):
    """An Impala engine that answers from a script instead of a warehouse."""

    def __init__(self, answers):
        super().__init__(ImpalaConfig("vw", 443, "svc", "pw"))
        self.answers, self.sql = answers, []

    def query(self, sql, caller=None, *, limit=1000):
        self.sql.append(sql)
        for prefix, answer in self.answers:
            if sql.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected SQL: {sql}")


def test_the_profiler_writes_impala_sql_with_ndv_and_backticks():
    engine = Recording([("SELECT COUNT(*)", QueryResult(["c0"] * 5, [(4, 4, 3, "a", "z")]))])
    stats = TableProfiler(engine).column_stats("sales", "orders", [{"name": "region", "type": "string"}])
    assert engine.sql == ["SELECT COUNT(*) AS c0, COUNT(`region`) AS c1, NDV(`region`) AS c2, "
                          "MIN(`region`) AS c3, MAX(`region`) AS c4 FROM `sales`.`orders`"]
    assert stats["columns"]["region"]["ndv"] == 3


def test_row_count_prefers_table_stats_and_falls_back_to_counting():
    stats = QueryResult(["#Rows", "Location"], [(40, "p1"), (2, "p2")])
    assert Recording([("SHOW TABLE STATS", stats)]).row_count("sales", "orders") == 42
    missing = QueryResult(["#Rows"], [(-1,)])
    engine = Recording([("SHOW TABLE STATS", missing), ("SELECT COUNT(*)", QueryResult(["c"], [(7,)]))])
    assert engine.row_count("sales", "orders") == 7
    assert engine.sql[-1] == "SELECT COUNT(*) FROM `sales`.`orders`"


def test_recent_queries_come_from_the_impala_query_log():
    log = QueryResult(["sql"], [("SELECT SUM(x) FROM t",), (None,)])
    assert Recording([("SELECT sql FROM sys.impala_query_log", log)]).recent_queries(10) == (
        "sys.impala_query_log", ["SELECT SUM(x) FROM t"])


def test_catalog_lookups_use_show_statements():
    engine = Recording([
        ("SHOW DATABASES", QueryResult(["name"], [("sales",)])),
        ("SHOW TABLES IN sales", QueryResult(["name"], [("orders",)])),
        ("DESCRIBE sales.orders", QueryResult(["name", "type", "comment"], [("region", "string", "")])),
    ])
    assert engine.databases() == ["sales"]
    assert engine.tables("sales") == ["orders"]
    assert engine.columns("sales", "orders") == [{"name": "region", "type": "string", "comment": ""}]
