"""helios init --sample (burndown 9.1): the TPC-DS store-sales star in DuckDB and its model,
registered and published. The model is the repository's TPC-DS model trimmed to the star;
the rows are generated the same every time and keep TPC-DS's arithmetic, so each metric
means what it says and every key joins."""
import io
import random
from pathlib import Path

import duckdb
import pytest
import yaml

from helios_cortex import sample
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.compiler import Compiler, SemanticRequest
from helios_cortex.engines import engine_for
from helios_cortex.ossie import SemanticModel, validate
from helios_cortex.registry.sql import SqlRegistry
from helios_cortex.settings import Settings

REPO = Path(__file__).resolve().parents[3]
STAR = {"store_sales", "date_dim", "item", "store", "customer_demographics", "household_demographics", "income_band"}


@pytest.fixture(scope="module")
def created(tmp_path_factory):
    return sample.create(tmp_path_factory.mktemp("project") / "helios.toml")


def db(created):
    return duckdb.connect(str(created.warehouse), read_only=True)


def test_the_model_is_the_repositorys_tpcds_model_trimmed_to_the_star():
    document = sample.model_document()
    assert validate(document) == []
    full = yaml.safe_load((REPO / "models/published/tpcds.ossie.yaml").read_text())
    full_model = SemanticModel(full)
    assert {d["name"] for d in document["datasets"]} == STAR
    assert document["datasets"] == [d for d in full["datasets"] if d["name"] in STAR]
    assert document["relationships"] == [r for r in full["relationships"] if r["from"] in STAR and r["to"] in STAR]
    assert document["metrics"] == [m for m in full["metrics"] if full_model.metrics[m["name"]].dataset in STAR]
    assert len(document["relationships"]) == len(document["metrics"]) == 7


def test_every_field_the_model_names_is_a_column_of_its_table():
    columns = {table: {name for name, _ in spec} for table, spec in sample.table_columns().items()}
    assert set(columns) == STAR
    model = SemanticModel(sample.model_document())
    for dataset in model.datasets.values():
        assert dataset.source == f"tpcds.{dataset.name}"
        assert {f.name for f in dataset.fields.values()} <= columns[dataset.name], dataset.name


def test_the_rows_are_the_same_every_time():
    first, second = sample.generate(random.Random(sample.SEED)), sample.generate(random.Random(sample.SEED))
    assert first == second
    assert first != sample.generate(random.Random(sample.SEED + 1))


def test_the_warehouse_holds_the_star(created):
    assert created.tables == {"store_sales": 60_000, "date_dim": 1826, "item": 300, "store": 10,
                              "customer_demographics": 280, "household_demographics": 600, "income_band": 20}
    with db(created) as con:
        years = con.execute("SELECT MIN(d_year), MAX(d_year), MIN(d_date), COUNT(DISTINCT d_date) "
                            "FROM tpcds.date_dim").fetchone()
        assert years[:2] == (1998, 2002) and str(years[2]) == "1998-01-01" and years[3] == 1826


@pytest.mark.parametrize(("child", "key", "parent", "parent_key"), [
    ("store_sales", "ss_sold_date_sk", "date_dim", "d_date_sk"),
    ("store_sales", "ss_item_sk", "item", "i_item_sk"),
    ("store_sales", "ss_store_sk", "store", "s_store_sk"),
    ("store_sales", "ss_cdemo_sk", "customer_demographics", "cd_demo_sk"),
    ("store_sales", "ss_hdemo_sk", "household_demographics", "hd_demo_sk"),
    ("household_demographics", "hd_income_band_sk", "income_band", "ib_income_band_sk"),
])
def test_every_key_joins(created, child, key, parent, parent_key):
    with db(created) as con:
        orphans = con.execute(f"SELECT COUNT(*) FROM tpcds.{child} c LEFT JOIN tpcds.{parent} p "
                              f"ON c.{key} = p.{parent_key} WHERE p.{parent_key} IS NULL").fetchone()[0]
    assert orphans == 0


def test_the_measures_keep_tpc_ds_arithmetic(created):
    with db(created) as con:
        broken = con.execute("""
            SELECT COUNT(*) FROM tpcds.store_sales s JOIN tpcds.store st ON s.ss_store_sk = st.s_store_sk
            WHERE ss_ext_sales_price <> ROUND(ss_quantity * ss_sales_price, 2)
               OR ss_ext_discount_amt <> ss_ext_list_price - ss_ext_sales_price
               OR ss_net_paid <> ss_ext_sales_price - ss_coupon_amt
               OR ss_net_paid_inc_tax <> ss_net_paid + ss_ext_tax
               OR ss_net_profit <> ss_net_paid - ss_ext_wholesale_cost
               OR ABS(ss_ext_tax - ss_net_paid * st.s_tax_percentage) > 0.01
               OR ss_sales_price > ss_list_price""").fetchone()[0]
        margin = con.execute("SELECT SUM(ss_net_profit) / SUM(ss_net_paid) FROM tpcds.store_sales").fetchone()[0]
    assert broken == 0
    assert 0.0 < margin < 0.3


def test_every_metric_compiles_and_runs_on_the_sample(created):
    settings = Settings.from_file(created.config)
    engine = engine_for(settings.connection())
    model = SemanticModel.load_published("tpcds", ArtifactStore.from_settings(settings))
    for metric in model.metrics:
        sql = Compiler(model).compile(SemanticRequest(metrics=[metric], dimensions=["tpcds.date_dim.d_year"]),
                                      dialect=engine.dialect).sql
        rows = engine.query(sql, limit=None).rows
        assert len(rows) == 5 and all(value is not None for _, value in rows), metric
    sql = Compiler(model).compile(SemanticRequest(metrics=["store_sales_revenue"],
                                                  dimensions=["tpcds.income_band.ib_upper_bound"]),
                                  dialect=engine.dialect).sql          # through household_demographics
    assert len(engine.query(sql, limit=None).rows) == 20


def test_the_model_is_registered_and_published(created):
    settings = Settings.from_file(created.config)
    assert settings.data_dir == created.config.parent / "helios-data"
    registry = SqlRegistry.from_settings(settings)
    assert registry.data_source("warehouse").connection_ref == "default"
    [model] = registry.models(status="published")
    assert model.model.id == "tpcds" and [r.data_source_id for r in model.model.data_sources] == ["warehouse"]
    [revision] = registry.semantic_revisions("tpcds")
    assert revision.id == created.revision_id
    assert ArtifactStore.from_settings(settings).active_revision("tpcds").revision_id == created.revision_id


def helios(*argv):
    from helios_cortex.cli import main

    out, err = io.StringIO(), io.StringIO()
    return main(list(argv), out=out, stderr=err), out.getvalue(), err.getvalue()


def test_helios_init_sample_and_what_it_refuses(tmp_path):
    config = tmp_path / "project" / "helios.toml"
    status, out, err = helios("init", "--sample", str(config))
    assert (status, err) == (0, "")
    assert "store_sales (60,000 rows)" in out and "published model tpcds" in out
    assert 'data_dir = "helios-data"' in config.read_text()

    status, _, err = helios("init", "--sample", str(config))
    assert status == 1 and "already exists; pass --force" in err
    config.unlink()                                   # the warehouse is still there
    status, _, err = helios("init", "--sample", str(config))
    assert status == 1 and "warehouse.duckdb already exists" in err and not config.exists()

    status, out, _ = helios("init", "--sample", "--force", str(config))
    assert status == 0 and "published model tpcds" in out
    with duckdb.connect(str(config.parent / "warehouse.duckdb"), read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM tpcds.store_sales").fetchone()[0] == 60_000
