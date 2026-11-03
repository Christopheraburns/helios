"""helios-cloudera registers its plugins under the names Settings use (burndown 3.3)."""
from helios_core import plugins
from helios_core.catalog import CatalogError, Harvester
from helios_core.engines import engine_for
from helios_core.settings import Settings

from helios_cloudera.atlas import AtlasClient, AtlasError
from helios_cloudera.impala import ImpalaEngine


def test_the_engine_and_catalog_load_by_their_settings_names():
    assert plugins.load(plugins.ENGINES, "impala") is ImpalaEngine
    assert plugins.load(plugins.CATALOGS, "atlas") is AtlasClient
    registered = {(info.group, info.name): info.distribution for info in plugins.installed()}
    assert registered[(plugins.ENGINES, "impala")] == "helios-cloudera"
    assert registered[(plugins.CATALOGS, "atlas")] == "helios-cloudera"
    assert registered[(plugins.ENGINES, "duckdb")] == "helios-core"


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

    [term] = Harvester(engine=None, catalog=Atlas()).harvest_glossary()
    assert (term["glossary"], term["name"], term["columns"]) == ("Sales", "Revenue", [])


def test_engine_for_builds_impala_from_its_connection():
    connection = Settings(connections={"cdw": {
        "engine": "impala", "host": "vw.example.com", "identity": "token"}}).connection("cdw")
    engine = engine_for(connection)
    assert isinstance(engine, ImpalaEngine)
    assert (engine.cfg.host, engine.cfg.user, engine.identity_mode.value) == ("vw.example.com", None, "token")
