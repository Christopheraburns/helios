"""The DuckDB TPC-DS sample the integration tests share: the trimmed star in helios-cortex's
harvest fixture with generated rows (``warehouse``), and settings with it registered and
the repository's published TPC-DS model published, as `helios publish` leaves it (``settings``)."""
import json
import random
from pathlib import Path

import duckdb
import pytest

from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.ossie import SemanticModel, dump_yaml
from helios_cortex.registry import SqlRegistry
from helios_cortex.settings import Settings

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
