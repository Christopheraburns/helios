"""Compiler tests against a synthetic TPC-DS star in DuckDB. Run: pytest tests/ (needs models/published/tpcds.ossie.yaml)."""
import json, os, random
import duckdb, pytest

from helios_core.ossie import SemanticModel
from helios_core.compiler import Compiler, SemanticRequest, Filter, Measure, CompileError

ROOT = os.environ.get("HELIOS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TYPES = {"bigint": "BIGINT", "int": "INTEGER", "string": "VARCHAR", "date": "DATE"}


@pytest.fixture(scope="module")
def model():
    return SemanticModel.load(os.path.join(ROOT, "models", "published", "tpcds.ossie.yaml"))


@pytest.fixture(scope="module")
def con():
    runs = sorted(d for d in os.listdir(os.path.join(ROOT, "runs")) if d[0].isdigit())
    h = json.load(open(os.path.join(ROOT, "runs", runs[-1], "harvest.json")))
    con = duckdb.connect(); con.execute("CREATE SCHEMA tpcds")
    keep = {"store_sales", "item", "date_dim", "store", "customer_demographics", "household_demographics", "income_band"}
    for t in h["tables"]:
        if t["table"] in keep:
            cols = ", ".join(f"{c['name']} {TYPES.get(c['type'].split('(')[0], c['type'].upper())}" for c in t["columns"])
            con.execute(f"CREATE TABLE tpcds.{t['table']} ({cols})")
    random.seed(1)
    con.execute("INSERT INTO tpcds.date_dim (d_date_sk, d_year, d_moy) SELECT i, 1998 + (i // 12), 1 + (i % 12) FROM range(1, 61) t(i)")
    con.execute("INSERT INTO tpcds.item (i_item_sk, i_brand_id, i_brand, i_manufact_id) SELECT i, 1000 + (i % 7), 'brand' || (i % 7), CASE WHEN i % 3 = 0 THEN 436 ELSE 1 END FROM range(1, 31) t(i)")
    con.execute("INSERT INTO tpcds.store (s_store_sk, s_state) SELECT i, ['TX','TN','SD'][1 + (i % 3)] FROM range(1, 7) t(i)")
    con.execute("INSERT INTO tpcds.income_band (ib_income_band_sk, ib_lower_bound) SELECT i, i * 10000 FROM range(1, 5) t(i)")
    con.execute("INSERT INTO tpcds.household_demographics (hd_demo_sk, hd_income_band_sk, hd_buy_potential) SELECT i, 1 + (i % 4), ['0-500','501-1000'][1 + (i % 2)] FROM range(1, 21) t(i)")
    con.execute("INSERT INTO tpcds.customer_demographics (cd_demo_sk, cd_gender) SELECT i, ['F','M'][1 + (i % 2)] FROM range(1, 41) t(i)")
    rows = [(random.randint(1, 60), random.randint(1, 30), random.randint(1, 40), random.randint(1, 20), random.randint(1, 6),
             random.randint(1, 5), round(random.uniform(1, 100), 2), round(random.uniform(1, 90), 2), round(random.uniform(-10, 30), 2)) for _ in range(5000)]
    con.executemany("INSERT INTO tpcds.store_sales (ss_sold_date_sk, ss_item_sk, ss_cdemo_sk, ss_hdemo_sk, ss_store_sk, ss_quantity, ss_ext_sales_price, ss_net_paid, ss_net_profit) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return con


def _norm(rows):
    return [tuple(round(float(x), 2) if isinstance(x, (float, int)) and not isinstance(x, bool) else x for x in r) for r in rows]


def test_q3_matches_kit(model, con):
    req = SemanticRequest(measures=[Measure("ss_ext_sales_price", "sum", "sum_agg")], dimensions=["d_year", "i_brand_id", "i_brand"],
                          filters=[Filter("i_manufact_id", "=", 436), Filter("d_moy", "=", 12)],
                          order_by=["d_year", "-sum_agg", "i_brand_id"], limit=100)
    ours = con.execute(Compiler(model).compile(req, dialect="duckdb").sql).fetchall()
    kit = con.execute("""select dt.d_year, item.i_brand_id, item.i_brand, sum(ss_ext_sales_price) sum_agg
        from tpcds.date_dim dt, tpcds.store_sales, tpcds.item
        where dt.d_date_sk = store_sales.ss_sold_date_sk and store_sales.ss_item_sk = item.i_item_sk
          and item.i_manufact_id = 436 and dt.d_moy = 12
        group by dt.d_year, item.i_brand, item.i_brand_id order by dt.d_year, sum_agg desc, item.i_brand_id limit 100""").fetchall()
    assert _norm(ours) == _norm(kit)


def test_snowflake_join(model, con):
    req = SemanticRequest(metrics=["Store Sales Revenue"], dimensions=["Buy Potential", "ib_lower_bound"], filters=[Filter("cd_gender", "=", "F")])
    c = Compiler(model).compile(req, dialect="duckdb")
    assert c.joins == ["store_sales__ss_cdemo_sk__customer_demographics", "store_sales__ss_hdemo_sk__household_demographics",
                       "household_demographics__hd_income_band_sk__income_band"]
    assert len(con.execute(c.sql).fetchall()) == 4


def test_default_and_pinned_relationship(model):
    base = SemanticRequest(metrics=["Catalog Sales Revenue"], dimensions=["d_year"])
    assert "cs_sold_date_sk" in Compiler(model).compile(base).sql
    pinned = SemanticRequest(metrics=["Catalog Sales Revenue"], dimensions=["d_year"], via={"date_dim": "catalog_sales__cs_ship_date_sk__date_dim"})
    assert "cs_ship_date_sk" in Compiler(model).compile(pinned).sql


def test_errors(model):
    with pytest.raises(CompileError):
        Compiler(model).compile(SemanticRequest(metrics=["Store Sales Revenue", "Web Sales Revenue"]))
    with pytest.raises(KeyError):
        Compiler(model).compile(SemanticRequest(metrics=["Store Sales Revenue"], dimensions=["State"]))  # ambiguous label
