"""The Helios facade and an evaluation on a DuckDB TPC-DS sample (burndown 6.7): the
repository's published TPC-DS model (models/published/tpcds.ossie.yaml) over the trimmed
star in helios-cortex's harvest fixture, with generated rows. Every answer is checked
against SQL written by hand on the same DuckDB file. Crosses helios-cortex (engine,
registry, artifacts), helios-serve (tools, evaluation) and helios-semantic (the facade)."""
import json
import random
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.ossie import SemanticModel, dump_yaml
from helios_cortex.registry import SqlRegistry
from helios_cortex.settings import Settings
from helios_semantic import Helios, HeliosError

REPO = Path(__file__).resolve().parents[2]
HARVEST = REPO / "packages/helios-cortex/tests/fixtures/tpcds-harvest.json"
MODEL = REPO / "models/published/tpcds.ossie.yaml"
TYPES = {"bigint": "BIGINT", "int": "INTEGER", "string": "VARCHAR", "date": "DATE"}
TABLES = {"store_sales", "item", "date_dim", "store"}


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    path = tmp_path_factory.mktemp("tpcds") / "warehouse.duckdb"
    harvest = json.loads(HARVEST.read_text())
    random.seed(7)
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE SCHEMA tpcds")
        for table in harvest["tables"]:
            if table["table"] in TABLES:
                columns = ", ".join(f"{c['name']} {TYPES.get(c['type'].split('(')[0], c['type'].upper())}"
                                    for c in table["columns"])
                db.execute(f"CREATE TABLE tpcds.{table['table']} ({columns})")
        db.execute("INSERT INTO tpcds.date_dim (d_date_sk, d_year, d_moy) "
                   "SELECT i, 1998 + (i // 12), 1 + (i % 12) FROM range(1, 61) t(i)")
        db.execute("INSERT INTO tpcds.item (i_item_sk, i_brand_id, i_brand) "
                   "SELECT i, 1000 + (i % 7), 'brand' || (i % 7) FROM range(1, 31) t(i)")
        db.execute("INSERT INTO tpcds.store (s_store_sk, s_state) "
                   "SELECT i, ['TX','TN','SD'][1 + (i % 3)] FROM range(1, 7) t(i)")
        rows = [(random.randint(1, 60), random.randint(1, 30), random.randint(1, 6), random.randint(1, 5),
                 round(random.uniform(1, 90), 2), round(random.uniform(-10, 30), 2)) for _ in range(2000)]
        db.executemany("INSERT INTO tpcds.store_sales (ss_sold_date_sk, ss_item_sk, ss_store_sk, ss_quantity, "
                       "ss_net_paid, ss_net_profit) VALUES (?, ?, ?, ?, ?, ?)", rows)
    return path


def by_hand(warehouse, sql):
    with duckdb.connect(str(warehouse), read_only=True) as db:
        return db.execute(sql).fetchall()


@pytest.fixture
def settings(tmp_path, warehouse):
    """The sample registered (data source, model) and its model published, as `helios publish` leaves it."""
    settings = Settings(data_dir=tmp_path / "data", connections={"default": {"engine": "duckdb",
                                                                             "path": str(warehouse)}})
    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(DataSource("warehouse", "TPC-DS sample", "duckdb", "default"))
    registry.save_model(Model("tpcds", "TPC-DS", (DataSourceReference("warehouse"),)), status="published")
    document = SemanticModel.load(str(MODEL)).doc
    ArtifactStore.from_settings(settings).write_published_ossie("tpcds", document, dump_yaml(document),
                                                                {"run_id": "sample"})
    return settings


def test_the_facade_answers_from_the_sample(settings, warehouse):
    helios = Helios.from_settings(settings)
    assert helios.models() == [{"name": "tpcds", "status": "published"}]
    assert helios.health()["status"] == "healthy"
    assert any(m["kind"] == "metric" for m in helios.search("store sales revenue"))

    result = helios.query(["Store Sales Revenue"], ["tpcds.date_dim.d_year"], limit=100)
    expected = by_hand(warehouse, "SELECT d.d_year, SUM(s.ss_net_paid) FROM tpcds.store_sales s "
                                  "JOIN tpcds.date_dim d ON s.ss_sold_date_sk = d.d_date_sk GROUP BY 1")
    assert sorted(result.rows) == sorted(expected)
    assert result.dataset == "tpcds.store_sales" and result.joins == ("tpcds.date_dim",)
    assert all(isinstance(row[1], Decimal) for row in result.rows)

    filtered = helios.query(["Store Sales Revenue", "Store Sales Quantity"], ["tpcds.store.s_state"],
                            filters=[{"column": "tpcds.date_dim.d_year", "op": "=", "value": 1999}])
    expected = by_hand(warehouse, "SELECT st.s_state, SUM(s.ss_net_paid), SUM(s.ss_quantity) FROM tpcds.store_sales s "
                                  "JOIN tpcds.date_dim d ON s.ss_sold_date_sk = d.d_date_sk "
                                  "JOIN tpcds.store st ON s.ss_store_sk = st.s_store_sk "
                                  "WHERE d.d_year = 1999 GROUP BY 1")
    assert sorted(filtered.rows) == sorted(expected)

    assert "store_sales" in helios.compile("Store Sales Revenue", dialect="trino").sql
    with pytest.raises(HeliosError, match="invalid_filter_value_type"):
        helios.query("Store Sales Revenue", filters=[{"column": "tpcds.date_dim.d_year", "op": "=",
                                                      "value": "1999-01-01"}])


def test_the_builtin_suite_scores_an_answer_from_the_sample(settings):
    pytest.importorskip("httpx", reason="the evaluation's LLM client needs helios-serve[eval]")
    from helios_cortex.llm import ToolCall, ToolTurn
    from helios_serve import load_suite, run_evaluation

    class Scripted:
        """Searches, then runs the metric the question names, then answers."""

        model = "scripted"

        def __init__(self, metric):
            self.rounds = [[("search_semantics", {"question": "store sales revenue"})],
                           [("run_query", {"metrics": [metric]})]]

        def tool_turn(self, system, messages, tools):
            if not self.rounds:
                return ToolTurn("Total store sales revenue is in the result.", (), "end_turn", 10, 5)
            return ToolTurn("", tuple(ToolCall(f"c{i}", name, args)
                                      for i, (name, args) in enumerate(self.rounds.pop(0))), "tool_use", 10, 5)

    suite = load_suite("tpcds")
    suite = {**suite, "questions": [q for q in suite["questions"] if q["id"] == "Q01"]}
    helios = Helios.from_settings(settings)
    report = run_evaluation(suite, helios.stores, {"right": Scripted("store_sales_revenue"),
                                                   "wrong": Scripted("store_sales_profit")})
    assert [(r.variant, r.accurate, r.completed) for r in report.results] == [("right", True, True),
                                                                               ("wrong", False, True)]
    assert report.metrics["right"]["grounding_rate"] > 0 and report.metrics["right"]["excess_tool_calls"] == 0
