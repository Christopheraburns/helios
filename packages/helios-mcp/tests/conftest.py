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
