"""A DuckDB warehouse with helios-serve's published shop model, as Stores and as a
helios.toml (for `helios mcp` in a subprocess)."""
import json
from pathlib import Path

import duckdb
import pytest

from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.ossie import dump_yaml
from helios_cortex.registry import SqlRegistry
from helios_cortex.settings import Settings

SHOP = json.loads((Path(__file__).resolve().parents[2] / "helios-serve/tests/fixtures/shop.ossie.json").read_text())


@pytest.fixture
def config(tmp_path):
    """helios.toml for a DuckDB warehouse whose shop model is registered and published."""
    warehouse = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(warehouse)) as db:
        db.execute("CREATE SCHEMA shop")
        db.execute("CREATE TABLE shop.customers AS SELECT i AS c_customer_id, "
                   "CASE WHEN i % 2 = 0 THEN 'east' ELSE 'west' END AS c_region FROM range(1, 21) t(i)")
        db.execute("CREATE TABLE shop.orders AS SELECT i AS o_order_id, 1 + i % 20 AS o_customer_id, "
                   "i * 1.5 AS o_amount FROM range(1, 101) t(i)")
    path = tmp_path / "helios.toml"
    path.write_text(f'data_dir = "{tmp_path / "data"}"\n\n[connections.default]\nengine = "duckdb"\n'
                    f'path = "{warehouse}"\n')
    settings = Settings.from_file(path)
    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(DataSource("warehouse", "Warehouse", "duckdb", "default"))
    registry.save_model(Model("shop", "Shop", (DataSourceReference("warehouse"),)), status="published")
    ArtifactStore.from_settings(settings).write_published_ossie("shop", SHOP, dump_yaml(SHOP), {"run_id": "r1"})
    return path


@pytest.fixture
def stores(config):
    from helios_serve import Stores, single_user_policy

    return Stores.from_settings(Settings.from_file(config), data_policy=single_user_policy())


REPO = Path(__file__).resolve().parents[3]
TPCDS_TYPES = {"bigint": "BIGINT", "int": "INTEGER", "string": "VARCHAR", "date": "DATE"}


@pytest.fixture(scope="module")
def tpcds_warehouse(tmp_path_factory):
    """The TPC-DS sample in DuckDB: the trimmed star of helios-cortex's harvest fixture with
    generated rows (store sales by date, item and store)."""
    import random

    harvest = json.loads((REPO / "packages/helios-cortex/tests/fixtures/tpcds-harvest.json").read_text())
    path = tmp_path_factory.mktemp("tpcds") / "warehouse.duckdb"
    random.seed(11)
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE SCHEMA tpcds")
        for table in harvest["tables"]:
            if table["table"] in {"store_sales", "item", "date_dim", "store"}:
                columns = ", ".join(f"{c['name']} {TPCDS_TYPES.get(c['type'].split('(')[0], c['type'].upper())}"
                                    for c in table["columns"])
                db.execute(f"CREATE TABLE tpcds.{table['table']} ({columns})")
        db.execute("INSERT INTO tpcds.date_dim (d_date_sk, d_year, d_moy) "
                   "SELECT i, 1998 + (i // 12), 1 + (i % 12) FROM range(1, 61) t(i)")
        db.execute("INSERT INTO tpcds.item (i_item_sk, i_brand) SELECT i, 'brand' || (i % 7) FROM range(1, 31) t(i)")
        db.execute("INSERT INTO tpcds.store (s_store_sk, s_state) "
                   "SELECT i, ['TX','TN','SD'][1 + (i % 3)] FROM range(1, 7) t(i)")
        db.executemany("INSERT INTO tpcds.store_sales (ss_sold_date_sk, ss_item_sk, ss_store_sk, ss_quantity, "
                       "ss_net_paid) VALUES (?, ?, ?, ?, ?)",
                       [(random.randint(1, 60), random.randint(1, 30), random.randint(1, 6), random.randint(1, 5),
                         round(random.uniform(1, 90), 2)) for _ in range(1000)])
    return path


@pytest.fixture
def tpcds_config(tmp_path, tpcds_warehouse):
    """A project helios.toml for the TPC-DS sample, its model registered and published."""
    from helios_cortex.ossie import SemanticModel

    project = tmp_path / "project"
    project.mkdir()
    path = project / "helios.toml"
    path.write_text(f'data_dir = "helios-data"\n\n[connections.default]\nengine = "duckdb"\n'
                    f'path = "{tpcds_warehouse}"\n')
    settings = Settings.from_file(path)
    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(DataSource("warehouse", "TPC-DS sample", "duckdb", "default"))
    registry.save_model(Model("tpcds", "TPC-DS", (DataSourceReference("warehouse"),)), status="published")
    document = SemanticModel.load(str(REPO / "models/published/tpcds.ossie.yaml")).doc
    ArtifactStore.from_settings(settings).write_published_ossie("tpcds", document, dump_yaml(document),
                                                                {"run_id": "sample"})
    return path
