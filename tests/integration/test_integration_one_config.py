"""One helios.toml for the facade and an agent's `helios mcp` (burndown 7.3). A project
starts from `helios init`'s starter file (DuckDB at the relative `warehouse.duckdb`); a
program uses the `Helios` facade and an agent launches `helios mcp`, from the project
(as Claude Code does) or from elsewhere with HELIOS_CONFIG (as an agent's config file
says). Every one of them sees the same model and gets the same answer, while the others
hold the warehouse open."""
import json
import subprocess
import sys
from pathlib import Path

import anyio
import duckdb
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.engines.duckdb_engine import DuckDBEngine
from helios_cortex.ossie import dump_yaml
from helios_cortex.registry import SqlRegistry
from helios_cortex.settings import Settings
from helios_semantic import Helios

REPO = Path(__file__).resolve().parents[2]
SHOP = json.loads((REPO / "packages/helios-serve/tests/fixtures/shop.ossie.json").read_text())
HELIOS = [sys.executable, "-m", "helios_cortex.cli"]
REVENUE_BY_REGION = {"metrics": ["revenue"], "dimensions": ["shop.customers.c_region"]}


@pytest.fixture
def project(tmp_path):
    """A project as the quickstart leaves it: `helios init`, a DuckDB warehouse beside the
    file, the shop model registered and published. The data directory is the project's.
    Returns the helios.toml."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run([*HELIOS, "init"], cwd=project, check=True, capture_output=True)
    config = project / "helios.toml"
    config.write_text(config.read_text().replace('# data_dir = "helios-data"', 'data_dir = "helios-data"'))
    with duckdb.connect(str(project / "warehouse.duckdb")) as db:
        db.execute("CREATE SCHEMA shop")
        db.execute("CREATE TABLE shop.customers AS SELECT i AS c_customer_id, "
                   "CASE WHEN i % 2 = 0 THEN 'east' ELSE 'west' END AS c_region FROM range(1, 21) t(i)")
        db.execute("CREATE TABLE shop.orders AS SELECT i AS o_order_id, 1 + i % 20 AS o_customer_id, "
                   "i * 1.5 AS o_amount FROM range(1, 101) t(i)")
    settings = Settings.from_file(config)
    assert settings.data_dir == project / "helios-data"
    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(DataSource("warehouse", "Warehouse", "duckdb", "default"))
    registry.save_model(Model("shop", "Shop", (DataSourceReference("warehouse"),)), status="published")
    ArtifactStore.from_settings(settings).write_published_ossie("shop", SHOP, dump_yaml(SHOP), {"run_id": "r1"})
    return config


def agent(cwd, env=None):
    """List the models and run revenue by region through `helios mcp`, as an agent would."""
    async def run():
        launch = StdioServerParameters(command=sys.executable, args=["-m", "helios_cortex.cli", "-q", "mcp"],
                                       cwd=str(cwd), env=env)
        async with Client(launch) as client:
            models = await client.call_tool("list_models", {})
            ran = await client.call_tool("run_query", REVENUE_BY_REGION)
            return models.structured_content, ran.structured_content

    return anyio.run(run)


def test_the_facade_and_helios_mcp_read_the_same_helios_toml(project, tmp_path, monkeypatch):
    config, project = project, project.parent
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(project)
    in_project = Helios.from_settings()                        # finds ./helios.toml
    monkeypatch.chdir(elsewhere)
    by_name = Helios.from_settings(config=config)
    expected = sorted(in_project.query(**REVENUE_BY_REGION).rows)
    assert sorted(by_name.query(**REVENUE_BY_REGION).rows) == expected == [("east", 3750), ("west", 3825)]
    assert in_project.models() == by_name.models() == [{"name": "shop", "status": "published"}]

    # The warehouse stays open in this process (a notebook mid-session) while the agents read it.
    holding = DuckDBEngine(str(project / "warehouse.duckdb"), read_only=True)
    assert holding.query("SELECT COUNT(*) FROM shop.orders").rows == [(100,)]

    from_project = agent(project)                                                    # Claude Code: the project
    from_elsewhere = agent(elsewhere, env={"HELIOS_CONFIG": str(config)})
    for models, ran in (from_project, from_elsewhere):
        assert models == {"models": [{"name": "shop", "status": "published"}]}
        assert sorted(ran["rows"]) == [[region, str(value)] for region, value in expected]

    assert not (elsewhere / "warehouse.duckdb").exists() and not (elsewhere / "helios-data").exists()
    holding.con.close()


def test_without_the_file_an_agent_sees_nothing_of_the_project(project, tmp_path):
    """Launched elsewhere with no HELIOS_CONFIG, helios mcp has another configuration: no shop model."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "helios.toml").write_text(f'data_dir = "{tmp_path / "other-data"}"\n')
    models, ran = agent(elsewhere)
    assert models == {"models": []}
    assert ran["error"] == "model_not_found"
