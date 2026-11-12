"""Every registry tool over MCP (burndown 7.2): names, descriptions and input schemas from
the registry, readOnlyHint from the effect, arguments checked against the schema, tool
errors as error results, and the caller: this user's own credentials by default, or a
caller whom the warehouse must enforce. In process through the SDK's Client, and over
real stdio through `helios mcp`."""
import sys

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from helios_cortex import Caller
from helios_serve import load_registry
from helios_mcp import build_server
from helios_mcp.server import HINTS


def session(server, *calls):
    """List the tools, then make each (name, arguments) call; returns (tools, results)."""
    async def run():
        async with Client(server) as client:
            listed = await client.list_tools()
            return listed.tools, [await client.call_tool(name, arguments) for name, arguments in calls]

    return anyio.run(run)


def test_every_registry_tool_is_offered_as_the_registry_describes_it(stores):
    registry = load_registry()
    tools, _ = session(build_server(stores))
    assert [t.name for t in tools] == list(registry)
    for offered in tools:
        entry = registry[offered.name]
        assert offered.description == entry.description
        assert offered.input_schema == entry.input_schema
        assert offered.annotations.read_only_hint is True and offered.annotations.destructive_hint is False


def test_every_effect_has_hints_and_none_writes():
    from helios_serve.registry import Effect

    assert set(HINTS) == {effect.value for effect in Effect}
    assert all(hints["read_only_hint"] and not hints["destructive_hint"] for hints in HINTS.values())


def test_the_tools_answer_from_the_published_model(stores):
    _, (models, search, compiled, ran) = session(
        build_server(stores),
        ("list_models", {}),
        ("search_semantics", {"question": "revenue by region"}),
        ("compile_query", {"metrics": ["revenue"], "dimensions": ["shop.customers.c_region"]}),
        ("run_query", {"metrics": ["revenue"], "dimensions": ["shop.customers.c_region"],
                       "filters": [{"column": "shop.customers.c_region", "op": "=", "value": "east"}]}))
    assert not any(r.is_error for r in (models, search, compiled, ran))
    assert models.structured_content == {"models": [{"name": "shop", "status": "published"}]}
    assert search.structured_content["matches"][0]["name"] == "revenue"
    assert compiled.structured_content["dialect"] == "duckdb"
    assert ran.structured_content["rows"] == [["east", "3750.0"]]
    assert '"rows"' in ran.content[0].text                                     # the same JSON, as text


def test_a_tool_error_is_an_error_result_the_agent_can_read(stores):
    _, (unknown_metric, unknown_model) = session(
        build_server(stores), ("run_query", {"metrics": ["profit"]}), ("describe_model", {"model": "nowhere"}))
    assert unknown_metric.is_error and unknown_metric.structured_content["error"] == "invalid_semantic_query"
    assert "search_semantics" in unknown_metric.structured_content["message"]
    assert unknown_model.is_error and unknown_model.structured_content["error"] == "model_not_found"


@pytest.mark.parametrize(("name", "arguments", "code", "says"), [
    ("run_query", {"metrics": "revenue"}, "invalid_tool_arguments", "metrics"),        # a string, not a list
    ("run_query", {}, "invalid_tool_arguments", "'metrics' is a required property"),
    ("run_query", {"metrics": ["revenue"], "sql": "DROP TABLE x"}, "invalid_tool_arguments", "sql"),
    ("run_query", {"metrics": ["revenue"], "filters": [{"column": "x"}]}, "invalid_tool_arguments", "op"),
    ("run_query", {"metrics": ["revenue"], "limit": "ten"}, "invalid_tool_arguments", "limit"),
    ("drop_table", {}, "unknown_tool", "drop_table"),
])
def test_arguments_are_checked_against_the_schema_before_the_tool_runs(stores, name, arguments, code, says):
    _, (result,) = session(build_server(stores), (name, arguments))
    assert result.is_error and result.structured_content["error"] == code
    assert says in result.structured_content["message"]


def test_a_raising_tool_says_so_without_internals(stores):
    class Broken:
        def sources(self):
            raise RuntimeError("/secret/path is gone")

    stores.models = Broken()
    _, (result,) = session(build_server(stores), ("list_models", {}))
    assert result.is_error and result.structured_content["error"] == "tool_failed"
    assert "secret" not in result.content[0].text


def test_a_caller_is_left_to_the_warehouse(stores):
    """A caller other than this user: the default policy, and DuckDB cannot run a query as alice."""
    from helios_cortex.data_authorization import DataPolicy

    stores.data_policy = DataPolicy()
    _, (as_alice,) = session(build_server(stores, caller=Caller("alice")), ("run_query", {"metrics": ["revenue"]}))
    assert as_alice.is_error and as_alice.structured_content["error"] == "data_authorization_denied"


def stdio(config, *args, env=None):
    return StdioServerParameters(command=sys.executable, args=["-m", "helios_cortex.cli", "-q", "--config",
                                                               str(config), "mcp", *args],
                                 cwd=str(config.parent), env=env)


def test_helios_mcp_serves_the_tools_over_stdio(config):
    tools, (ran,) = session(stdio(config), ("run_query", {"metrics": ["revenue", "order_count"]}))
    assert {t.name for t in tools} == set(load_registry())
    assert not ran.is_error and ran.structured_content["rows"] == [["7575.0", 100]]


def test_helios_mcp_as_a_caller_lets_the_warehouse_decide(config):
    _, (as_alice,) = session(stdio(config, "--as", "alice"), ("run_query", {"metrics": ["revenue"]}))
    assert as_alice.structured_content["error"] == "data_authorization_denied"
    _, (with_token,) = session(stdio(config, env={"HELIOS_CALLER_TOKEN": "a-jwt"}),
                               ("run_query", {"metrics": ["revenue"]}))
    assert with_token.structured_content["error"] == "data_authorization_denied"   # a service connection
