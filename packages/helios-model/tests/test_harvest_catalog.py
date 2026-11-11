"""The harvester reads a glossary from any catalog plugin (helios_cortex.catalog.GlossaryCatalog),
and only from one that has a glossary."""
from helios_cortex.catalog import CatalogError
from helios_cortex.engines.duckdb_engine import DuckDBEngine
from helios_model.harvest import TableHarvester


class Glossary:
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)

    def list_glossaries(self):
        return [{"guid": "g1", "name": "Sales"}, {"guid": "g2", "name": "HR"}]

    def list_terms(self, glossary_guid):
        return {"g1": [{"guid": "t1", "name": "Revenue", "shortDescription": "Money in"},
                       {"guid": "t2", "name": "Margin"}],
                "g2": [{"guid": "t3", "name": "Headcount"}]}[glossary_guid]

    def assigned_entities(self, term_guid):
        if term_guid in self.fail_on:
            raise CatalogError("unavailable")
        return [{"displayText": f"sales.orders.{term_guid}"}]


def test_no_catalog_means_no_glossary():
    assert TableHarvester(engine=None).harvest_glossary() == []


def test_terms_come_with_their_assigned_columns_from_named_glossaries():
    terms = TableHarvester(engine=None, catalog=Glossary()).harvest_glossary(["Sales"])
    assert [(t["name"], t["short_description"], t["columns"]) for t in terms] == [
        ("Revenue", "Money in", ["sales.orders.t1"]),
        ("Margin", "", ["sales.orders.t2"]),
    ]


def test_a_failing_assignment_lookup_keeps_the_term_without_columns():
    terms = TableHarvester(engine=None, catalog=Glossary(fail_on={"t1"})).harvest_glossary(["Sales"])
    assert [t["columns"] for t in terms] == [[], ["sales.orders.t2"]]


class Lineage:
    """A lineage-only catalog (say, OpenLineage): no glossary."""

    def column_lineage(self, database, table, column, depth=3):
        return None


def test_the_harvest_takes_the_glossary_only_from_a_catalog_that_has_one():
    engine = DuckDBEngine()
    engine.query("CREATE SCHEMA sales")
    engine.query("CREATE TABLE sales.orders (amount DOUBLE)")

    class Revenue(Glossary):
        def list_glossaries(self):
            return [{"guid": "g", "name": "Sales"}]

        def list_terms(self, glossary_guid):
            return [{"guid": "t", "name": "Revenue"}]

        def assigned_entities(self, term_guid):
            return [{"displayText": "sales.orders.amount"}]

    without = TableHarvester(engine).run(["sales"])
    lineage_only = TableHarvester(engine, Lineage()).run(["sales"])
    with_glossary = TableHarvester(engine, Revenue()).run(["sales"])

    assert [t["table"] for t in without["tables"]] == ["orders"]           # tables come from the engine
    assert (without["catalog_capabilities"], without["glossary_terms"]) == ([], [])
    assert (lineage_only["catalog_capabilities"], lineage_only["glossary_terms"]) == (["lineage"], [])
    assert with_glossary["catalog_capabilities"] == ["glossary"]
    assert [(t["name"], t["columns"]) for t in with_glossary["glossary_terms"]] == [("Revenue", ["sales.orders.amount"])]
