"""The harvester reads a glossary from any catalog plugin (helios_core.catalog.GlossaryCatalog)."""
from helios_core.catalog import CatalogError, Harvester


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
    assert Harvester(engine=None).harvest_glossary() == []


def test_terms_come_with_their_assigned_columns_from_named_glossaries():
    terms = Harvester(engine=None, catalog=Glossary()).harvest_glossary(["Sales"])
    assert [(t["name"], t["short_description"], t["columns"]) for t in terms] == [
        ("Revenue", "Money in", ["sales.orders.t1"]),
        ("Margin", "", ["sales.orders.t2"]),
    ]


def test_a_failing_assignment_lookup_keeps_the_term_without_columns():
    terms = Harvester(engine=None, catalog=Glossary(fail_on={"t1"})).harvest_glossary(["Sales"])
    assert [t["columns"] for t in terms] == [[], ["sales.orders.t2"]]
