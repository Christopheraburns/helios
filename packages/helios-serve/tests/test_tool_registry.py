"""The tool registry over the ``helios.tools`` entry point (burndown 6.3): the seven tools
with their descriptions, input schemas from type hints and effects; explain_lineage only
beside a catalog with lineage; a broken registration fails loudly."""
import importlib.util
import json
from importlib.metadata import EntryPoint
from typing import Annotated, Any, Literal, TypedDict

import pytest

from helios_cortex import plugins
from helios_serve import Effect, Registry, load_registry, tool, tools
from helios_serve.registry import input_schema, schema_for, tool_from

from test_tools import ALICE

SEVEN = {"list_models", "describe_model", "search_semantics", "describe", "compile_query", "run_query",
         "explain_lineage"}


def test_the_registry_lists_seven_tools_with_a_lineage_catalog():
    assert set(load_registry(catalog_capabilities={"lineage"})) == SEVEN
    assert set(load_registry(catalog_capabilities=())) == SEVEN - {"explain_lineage"}


def test_explain_lineage_is_offered_when_an_installed_catalog_has_lineage():
    """helios-cloudera's Atlas has lineage; a base install (cortex and serve alone) has no catalog."""
    with_cloudera = importlib.util.find_spec("helios_cloudera") is not None
    assert ("explain_lineage" in load_registry()) is with_cloudera


def test_a_catalog_plugin_offers_lineage_by_its_methods(monkeypatch):
    real = plugins._entry_points

    def entry_points(group):
        if group == plugins.CATALOGS:
            return (EntryPoint("glossary_only", "test_tool_registry:GlossaryOnly", group),
                    EntryPoint("broken", "helios_no_such_module:Catalog", group))
        return real(group)

    monkeypatch.setattr(plugins, "_entry_points", entry_points)
    assert "explain_lineage" not in load_registry()          # a glossary is not lineage; broken offers nothing

    monkeypatch.setattr(plugins, "_entry_points", lambda group: (
        (EntryPoint("lineage", "test_tool_registry:WithLineage", group),) if group == plugins.CATALOGS else real(group)))
    assert "explain_lineage" in load_registry()


class GlossaryOnly:
    def list_glossaries(self): ...
    def list_terms(self, glossary_guid): ...
    def assigned_entities(self, term_guid): ...


class WithLineage:
    def column_lineage(self, database, table, column, depth=3): ...


def test_only_run_query_executes_sql():
    registry = load_registry(catalog_capabilities={"lineage"})
    assert {t.name for t in registry.tools() if t.effect.executes_sql} == {"run_query"}
    assert registry["run_query"].effect is Effect.QUERY
    # The read-only metadata server (Daedalus, D.4): every tool but run_query.
    assert {t.name for t in registry.tools(effects={"read"})} == SEVEN - {"run_query"}


def test_each_tool_is_described_for_an_agent():
    for entry in load_registry(catalog_capabilities={"lineage"}).values():
        described = entry.as_dict()
        assert set(described) == {"name", "description", "input_schema", "effect"}
        assert json.loads(json.dumps(described)) == described            # JSON as it stands
        assert len(entry.description) > 20 and entry.distribution == "helios-serve"
        assert entry.function is getattr(tools, entry.name)


def _walk(schema):
    yield schema
    for child in schema.get("properties", {}).values():
        yield from _walk(child)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_input_schemas_come_from_the_type_hints():
    registry = load_registry(catalog_capabilities={"lineage"})
    run = registry["run_query"].input_schema
    assert run["required"] == ["metrics"] and run["additionalProperties"] is False
    assert list(run["properties"]) == ["metrics", "dimensions", "filters", "limit", "model"]
    assert run["properties"]["metrics"]["type"] == "array"
    assert run["properties"]["metrics"]["items"] == {"type": "string"}
    assert run["properties"]["limit"]["default"] == 100
    item = run["properties"]["filters"]["items"]
    assert item["required"] == ["op"] and set(item["properties"]) == {"column", "field", "op", "value"}
    assert item["properties"]["value"].keys() == {"description"}                 # any JSON value
    assert "dialect" in registry["compile_query"].input_schema["properties"]
    assert registry["list_models"].input_schema["properties"] == {}
    assert registry["search_semantics"].input_schema["required"] == ["question"]
    for entry in registry.values():
        for node in _walk(entry.input_schema):
            # The subset MCP and OpenAPI 3.0 share: an optional argument is not a null type.
            assert node.get("type") != "null" and not isinstance(node.get("type"), list)
            assert "anyOf" not in node and "oneOf" not in node
        assert "caller" not in entry.input_schema["properties"] and "stores" not in entry.input_schema["properties"]


def test_the_schemas_accept_what_the_tools_take_and_refuse_the_rest():
    jsonschema = pytest.importorskip("jsonschema")
    run = load_registry()["run_query"].input_schema
    jsonschema.validate({"metrics": ["revenue"], "dimensions": ["shop.customers.c_region"],
                         "filters": [{"column": "shop.customers.c_region", "op": "=", "value": "east"},
                                     {"field": "shop.orders.o_amount", "op": "BETWEEN", "value": [1, 5]}],
                         "limit": 5, "model": "shop"}, run)
    for wrong in ({}, {"metrics": "revenue"}, {"metrics": ["revenue"], "filters": [{"column": "x"}]},
                  {"metrics": ["revenue"], "sql": "DROP TABLE x"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(wrong, run)


def test_a_registered_tool_runs_as_the_function_does(stores):
    registry = load_registry()
    assert registry.call("list_models", ALICE, stores) == tools.list_models(ALICE, stores)
    ran = registry["run_query"](ALICE, stores, metrics=["revenue"], dimensions=["shop.customers.c_region"])
    assert ran["columns"] and ran["rows"]
    assert stores.audit.records[-1].action == "run_query"                    # audited, as the function is
    with pytest.raises(KeyError):
        registry.call("drop_table", ALICE, stores)


# ---------------------------------------------------------------------------- the schema builder
class Point(TypedDict):
    x: int
    y: Annotated[float, "Up"]


def test_schema_for_each_supported_hint():
    assert schema_for(str) == {"type": "string"}
    assert schema_for(bool | None) == {"type": "boolean"}
    assert schema_for(Annotated[int, "How many"]) == {"type": "integer", "description": "How many"}
    assert schema_for(Literal["a", "b"]) == {"type": "string", "enum": ["a", "b"]}
    assert schema_for(tuple[str, ...]) == {"type": "array", "items": {"type": "string"}}
    assert schema_for(dict[str, Any]) == {"type": "object"}
    assert schema_for(Any) == {}
    assert schema_for(list[Point]) == {"type": "array", "items": {
        "type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "number", "description": "Up"}},
        "required": ["x", "y"], "additionalProperties": False}}
    for unsupported in (int | str, Literal["a", 1], tuple[int, str], set):
        with pytest.raises(TypeError):
            schema_for(unsupported)


def test_a_tool_signature_the_schema_cannot_express_fails_at_definition():
    with pytest.raises(TypeError, match="caller, stores"):
        tool(effect="read", description="x")(lambda stores, caller: {})
    with pytest.raises(TypeError, match="named argument"):
        @tool(effect="read", description="x")
        def splat(caller, stores, *names: str): ...
    with pytest.raises(TypeError, match="type hint"):
        @tool(effect="read", description="x")
        def untyped(caller, stores, *, name): ...
    with pytest.raises(TypeError, match="description"):
        @tool(effect="read")
        def undocumented(caller, stores): ...
    with pytest.raises(ValueError):
        tool(effect="write")


def test_a_marked_function_stays_callable_and_describes_itself():
    @tool(effect="query", requires={"lineage"})
    def count(caller, stores, *, table: Annotated[str, "database.table"], limit: int = 5) -> dict:
        """Count a table's rows."""
        return {"table": table, "limit": limit}

    assert count(None, None, table="shop.orders") == {"table": "shop.orders", "limit": 5}
    described = tool_from(count)
    assert (described.name, described.description, described.effect, described.requires) == (
        "count", "Count a table's rows.", Effect.QUERY, frozenset({"lineage"}))
    assert input_schema(count) == {"type": "object", "required": ["table"], "additionalProperties": False,
                                   "properties": {"table": {"type": "string", "description": "database.table"},
                                                  "limit": {"type": "integer", "default": 5}}}
    with pytest.raises(ValueError, match="twice"):
        Registry([described, described])


def test_a_broken_registration_fails_loudly(monkeypatch):
    real = plugins._entry_points

    def with_tools(*extra):
        def entry_points(group):
            found = real(group)
            return found + tuple(EntryPoint(n, v, group) for n, v in extra) if group == plugins.TOOLS else found
        return entry_points

    monkeypatch.setattr(plugins, "_entry_points", with_tools(("error", "helios_serve.tools:error")))
    with pytest.raises(plugins.PluginError, match="not marked"):
        load_registry()
    monkeypatch.setattr(plugins, "_entry_points", with_tools(("lookup", "helios_serve.tools:describe")))
    with pytest.raises(plugins.PluginError, match="another name"):
        load_registry()
    monkeypatch.setattr(plugins, "_entry_points", with_tools(("describe", "helios_serve.tools:describe_model")))
    with pytest.raises(plugins.PluginError, match="more than once"):
        load_registry()
    monkeypatch.setattr(plugins, "_entry_points", with_tools(("missing", "helios_no_such_module:missing")))
    with pytest.raises(plugins.PluginLoadError):
        load_registry()
