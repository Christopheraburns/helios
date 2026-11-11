"""Catalog plugins and their optional capabilities, glossary and lineage (burndown 3.7)."""
import sys
import types
from importlib.metadata import EntryPoint

import pytest

from helios_core import plugins
from helios_core.catalog import (
    GLOSSARY,
    LINEAGE,
    ColumnLineage,
    Harvester,
    capabilities,
    catalog_for,
)
from helios_core.engines.duckdb_engine import DuckDBEngine
from helios_core.settings import Settings


class Lineage:
    """A lineage-only catalog (say, OpenLineage): no glossary."""

    def __init__(self, options=None):
        self.options = options or {}

    @classmethod
    def from_catalog(cls, catalog):
        return cls(catalog.options)

    def column_lineage(self, database, table, column, depth=3):
        if column != "amount":
            return None
        return ColumnLineage(f"{database}.{table}.{column}", {"a": {"type": "column", "name": "amount"},
                                                             "b": {"type": "process", "name": "etl"}}, [("b", "a")])


class Glossary:
    @classmethod
    def from_catalog(cls, catalog):
        return cls()

    def list_glossaries(self):
        return [{"guid": "g", "name": "Sales"}]

    def list_terms(self, glossary_guid):
        return [{"guid": "t", "name": "Revenue"}]

    def assigned_entities(self, term_guid):
        return [{"displayText": "sales.orders.amount"}]


class Nothing:
    @classmethod
    def from_catalog(cls, catalog):
        return cls()


@pytest.fixture
def registered(monkeypatch):
    module = types.ModuleType("fake_catalogs")
    module.Lineage, module.Glossary, module.Nothing, module.Bare = Lineage, Glossary, Nothing, object
    monkeypatch.setitem(sys.modules, "fake_catalogs", module)
    entries = [EntryPoint(name, f"fake_catalogs:{target}", plugins.CATALOGS)
               for name, target in [("lineage", "Lineage"), ("glossary", "Glossary"), ("nothing", "Nothing"), ("bare", "Bare")]]
    monkeypatch.setattr(plugins, "_entry_points", lambda group: tuple(e for e in entries if e.group == group))


def catalog(name, **options):
    return Settings(catalogs={"default": {"catalog": name, **options}}).catalog()


def test_capabilities_are_what_a_catalog_implements():
    assert capabilities(Glossary()) == {GLOSSARY}
    assert capabilities(Lineage()) == {LINEAGE}
    assert capabilities(None) == frozenset()

    class Both(Glossary, Lineage):
        pass

    assert capabilities(Both()) == {GLOSSARY, LINEAGE}


def test_catalog_for_builds_the_named_plugin_from_its_settings(registered):
    built = catalog_for(catalog("lineage", endpoint="https://lineage.example"))
    assert isinstance(built, Lineage)
    assert built.options == {"endpoint": "https://lineage.example"}
    assert built.column_lineage("sales", "orders", "amount").as_dict() == {
        "column": "sales.orders.amount",
        "entities": {"a": {"type": "column", "name": "amount"}, "b": {"type": "process", "name": "etl"}},
        "edges": [{"from": "b", "to": "a"}],
    }
    assert catalog_for(None) is None  # no [catalogs] entry: no catalog, and harvesting still works


def test_catalog_for_refuses_a_plugin_that_is_not_a_catalog(registered):
    with pytest.raises(plugins.PluginError, match="has no from_catalog"):
        catalog_for(catalog("bare"))
    with pytest.raises(plugins.PluginError, match="neither a glossary nor lineage"):
        catalog_for(catalog("nothing"))
    with pytest.raises(plugins.PluginNotFound, match="no plugin 'atlas'"):
        catalog_for(catalog("atlas"))


def test_the_harvest_takes_the_glossary_only_from_a_catalog_that_has_one():
    engine = DuckDBEngine()
    engine.query("CREATE SCHEMA sales")
    engine.query("CREATE TABLE sales.orders (amount DOUBLE)")

    without = Harvester(engine).run(["sales"])
    lineage_only = Harvester(engine, Lineage()).run(["sales"])
    with_glossary = Harvester(engine, Glossary()).run(["sales"])

    assert [t["table"] for t in without["tables"]] == ["orders"]           # tables come from the engine
    assert (without["catalog_capabilities"], without["glossary_terms"]) == ([], [])
    assert (lineage_only["catalog_capabilities"], lineage_only["glossary_terms"]) == (["lineage"], [])
    assert with_glossary["catalog_capabilities"] == ["glossary"]
    assert [(t["name"], t["columns"]) for t in with_glossary["glossary_terms"]] == [("Revenue", ["sales.orders.amount"])]
