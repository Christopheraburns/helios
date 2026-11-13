"""The sample model over MCP (burndown 7.4, 7.5 and the Phase 7 gate): an in-process MCP
client lists the tools and runs compile_query and run_query on the TPC-DS sample; and each
agent's configuration from `helios mcp --print-config`, read back and launched exactly as
that agent would, lists the tools and answers the same query. Answers are checked against
SQL written by hand on the same DuckDB file."""
import subprocess
import sys

import anyio
import duckdb
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from helios_cortex.settings import Settings
from helios_serve import Stores, load_registry, single_user_policy
from helios_mcp import build_server
from helios_mcp.agents import AGENTS, server_entry

BY_YEAR = {"metrics": ["Store Sales Revenue", "Store Sales Quantity"], "dimensions": ["tpcds.date_dim.d_year"]}
HAND = ("SELECT d.d_year, SUM(s.ss_net_paid), SUM(s.ss_quantity) FROM tpcds.store_sales s "
        "JOIN tpcds.date_dim d ON s.ss_sold_date_sk = d.d_date_sk GROUP BY 1")


def expected(warehouse):
    with duckdb.connect(str(warehouse), read_only=True) as db:
        return sorted([year, str(revenue), quantity] for year, revenue, quantity in db.execute(HAND).fetchall())


def ask(server):
    async def run():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            compiled = await client.call_tool("compile_query", BY_YEAR)
            trino = await client.call_tool("compile_query", {**BY_YEAR, "dialect": "trino"})
            ran = await client.call_tool("run_query", BY_YEAR)
            return tools, compiled, trino, ran

    return anyio.run(run)


def test_an_in_process_client_compiles_and_runs_on_the_sample(tpcds_config, tpcds_warehouse):
    stores = Stores.from_settings(Settings.from_file(tpcds_config), data_policy=single_user_policy())
    tools, compiled, trino, ran = ask(build_server(stores))
    assert {t.name for t in tools} == set(load_registry())
    assert not any(r.is_error for r in (compiled, trino, ran))
    assert compiled.structured_content["dialect"] == "duckdb" and trino.structured_content["dialect"] == "trino"
    assert compiled.structured_content["dataset"] == "tpcds.store_sales"
    assert compiled.structured_content["joins"] == ["tpcds.date_dim"]
    assert ran.structured_content["sql"] == compiled.structured_content["sql"]
    assert sorted(ran.structured_content["rows"]) == expected(tpcds_warehouse)


def print_config(config, agent, *extra, cwd):
    return subprocess.run([sys.executable, "-m", "helios_cortex.cli", "--config", str(config), "mcp",
                           "--print-config", agent, *extra], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_each_agents_configuration_launches_a_server_that_answers(agent, tpcds_config, tpcds_warehouse, tmp_path):
    """The gate: an agent configured with the printed snippet, started in another directory."""
    elsewhere = tmp_path / "agent-cwd"
    elsewhere.mkdir()
    printed = print_config(tpcds_config.relative_to(tmp_path), agent, cwd=tmp_path).stdout
    command, args, env = server_entry(agent, printed)
    assert str(tpcds_config) in args                                   # absolute, wherever it was printed
    launch = StdioServerParameters(command=command, args=args, env=env or None, cwd=str(elsewhere))
    tools, _, _, ran = ask(launch)
    assert {t.name for t in tools} == set(load_registry())
    assert not ran.is_error and sorted(ran.structured_content["rows"]) == expected(tpcds_warehouse)
    assert list(elsewhere.iterdir()) == []                            # nothing written where the agent runs


def test_a_snippet_carries_the_caller_but_never_a_token(tpcds_config, tmp_path, monkeypatch):
    monkeypatch.setenv("HELIOS_CALLER_TOKEN", "a-secret-jwt")
    printed = print_config(tpcds_config, "claude-code", "--as", "alice", cwd=tmp_path).stdout
    command, args, env = server_entry("claude-code", printed)
    assert args[-2:] == ["--as", "alice"] and env == {}
    assert "a-secret-jwt" not in printed


def test_print_config_needs_a_config_file(tmp_path):
    missing = subprocess.run([sys.executable, "-m", "helios_cortex.cli", "mcp", "--print-config", "cursor"],
                             cwd=tmp_path, capture_output=True, text=True)
    assert missing.returncode == 1 and "helios init" in missing.stderr
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    unrelated = subprocess.run([sys.executable, "-m", "helios_cortex.cli", "mcp", "--print-config", "cursor"],
                               cwd=tmp_path, capture_output=True, text=True)
    assert unrelated.returncode == 1 and "[tool.helios]" in unrelated.stderr


def test_the_readmes_snippets_are_what_print_config_prints():
    """The guide's four examples parse as each agent reads them and match --print-config's shape."""
    import re
    from pathlib import Path

    from helios_mcp.agents import snippet

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    blocks = dict(zip(("claude-code", "cursor", "goose", "vibe"),
                      re.findall(r"```(?:json|yaml|toml)\n(.*?)```", readme, re.S)))
    command, args = "/path/to/.venv/bin/helios", ["--config", "/path/to/project/helios.toml", "mcp"]
    for agent, text in blocks.items():
        assert server_entry(agent, text) == server_entry(agent, snippet(agent, command, args)) == (command, args, {})
    assert set(blocks) == set(AGENTS)
