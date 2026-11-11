"""helios-cloudera registers its plugins under the names Settings use (burndown 3.3)."""
from helios_cortex import plugins
from helios_cortex.catalog import GLOSSARY, LINEAGE, CatalogError, capabilities, catalog_for
from helios_cortex.engines import engine_for
from helios_cortex.settings import Settings

from helios_cloudera.atlas import AtlasClient, AtlasError
from helios_cloudera.impala import ImpalaEngine
from helios_model.harvest import TableHarvester


def test_the_engine_and_catalog_load_by_their_settings_names():
    assert plugins.load(plugins.ENGINES, "impala") is ImpalaEngine
    assert plugins.load(plugins.CATALOGS, "atlas") is AtlasClient
    registered = {(info.group, info.name): info.distribution for info in plugins.installed()}
    assert registered[(plugins.ENGINES, "impala")] == "helios-cloudera"
    assert registered[(plugins.CATALOGS, "atlas")] == "helios-cloudera"
    assert registered[(plugins.ENGINES, "duckdb")] == "helios-cortex"


def test_a_workbench_configuration_finds_every_plugin():
    plugins.require_configured(Settings(
        connections={"default": {"engine": "impala", "host": "vw", "user": "svc", "password": "pw"}},
        catalogs={"default": {"catalog": "atlas", "base_url": "https://dl/atlas", "user": "a", "password": "pw"}},
    ))


def test_atlas_errors_are_catalog_errors_so_the_harvester_tolerates_them():
    assert issubclass(AtlasError, CatalogError)

    class Atlas:
        def list_glossaries(self):
            return [{"guid": "g", "name": "Sales"}]

        def list_terms(self, glossary_guid):
            return [{"guid": "t", "name": "Revenue"}]

        def assigned_entities(self, term_guid):
            raise AtlasError(403, "forbidden")

    [term] = TableHarvester(engine=None, catalog=Atlas()).harvest_glossary()
    assert (term["glossary"], term["name"], term["columns"]) == ("Sales", "Revenue", [])


def test_engine_for_builds_impala_from_its_connection():
    connection = Settings(connections={"cdw": {
        "engine": "impala", "host": "vw.example.com", "identity": "token"}}).connection("cdw")
    engine = engine_for(connection)
    assert isinstance(engine, ImpalaEngine)
    assert (engine.cfg.host, engine.cfg.user, engine.identity_mode.value) == ("vw.example.com", None, "token")


def test_atlas_is_a_catalog_with_a_glossary_and_lineage(monkeypatch):
    atlas = catalog_for(Settings(catalogs={"default": {
        "catalog": "atlas", "base_url": "https://dl/atlas/", "user": "a", "password": "pw"}}).catalog())
    assert isinstance(atlas, AtlasClient)
    assert atlas.base == "https://dl/atlas"
    assert capabilities(atlas) == {GLOSSARY, LINEAGE}

    graph = {"guidEntityMap": {"c1": {"typeName": "hive_column", "displayText": "amount"},
                               "p1": {"typeName": "hive_process", "displayText": "load_orders"}},
             "relations": [{"fromEntityId": "p1", "toEntityId": "c1"}]}
    monkeypatch.setattr(atlas, "find_column", lambda db, table, column: ("c1", "hive_column") if column == "amount" else None)
    monkeypatch.setattr(atlas, "lineage", lambda guid, depth=3: graph)
    lineage = atlas.column_lineage("sales", "orders", "amount")
    assert lineage.as_dict() == {
        "column": "sales.orders.amount",
        "entities": {"c1": {"type": "hive_column", "name": "amount"}, "p1": {"type": "hive_process", "name": "load_orders"}},
        "edges": [{"from": "p1", "to": "c1"}],
    }
    assert atlas.column_lineage("sales", "orders", "nope") is None
