"""Plugins load from entry points by group and name, and fail clearly (burndown 3.1)."""
from importlib.metadata import EntryPoint

import pytest

from helios_cortex import plugins
from helios_cortex.engines.duckdb_engine import DuckDBEngine
from helios_cortex.settings import Settings


@pytest.fixture
def registered(monkeypatch):
    """Replace the installed entry points with ``(group, name, target)`` triples."""

    def register(*triples):
        entries = [EntryPoint(name, target, group) for group, name, target in triples]
        monkeypatch.setattr(
            plugins, "_entry_points", lambda group: tuple(e for e in entries if e.group == group)
        )

    return register


def test_helios_cortex_registers_its_own_plugins():
    assert "duckdb" in plugins.names(plugins.ENGINES)
    assert plugins.load(plugins.ENGINES, "duckdb") is DuckDBEngine
    [duckdb] = [info for info in plugins.installed(plugins.ENGINES) if info.name == "duckdb"]
    assert duckdb.distribution == "helios-cortex"
    assert duckdb.target == "helios_cortex.engines.duckdb_engine:DuckDBEngine"


def test_installed_reports_every_group_in_order_without_importing(registered):
    registered(
        (plugins.TOOLS, "lineage", "never_imported_module:tool"),
        (plugins.ENGINES, "warehouse", "never_imported_module:Engine"),
        (plugins.ENGINES, "duckdb", "helios_cortex.engines.duckdb_engine:DuckDBEngine"),
    )
    assert [(i.group, i.name) for i in plugins.installed()] == [
        (plugins.ENGINES, "duckdb"),
        (plugins.ENGINES, "warehouse"),
        (plugins.TOOLS, "lineage"),
    ]
    assert plugins.names(plugins.STORES) == ()


def test_a_missing_plugin_names_what_is_installed_and_what_to_install(registered):
    registered((plugins.ENGINES, "duckdb", "helios_cortex.engines.duckdb_engine:DuckDBEngine"))
    with pytest.raises(plugins.PluginNotFound) as raised:
        plugins.load(plugins.ENGINES, "impala")
    assert str(raised.value) == (
        "no plugin 'impala' in helios.engines; installed: duckdb; install helios-cloudera"
    )
    assert isinstance(raised.value, LookupError)


def test_a_plugin_that_fails_to_import_says_why(registered):
    registered((plugins.ENGINES, "broken", "helios_module_that_is_not_installed:Engine"))
    with pytest.raises(plugins.PluginLoadError, match="'broken' in helios.engines .* ModuleNotFoundError") as raised:
        plugins.load(plugins.ENGINES, "broken")
    assert isinstance(raised.value.__cause__, ModuleNotFoundError)


def test_two_packages_registering_one_name_is_an_error(registered):
    registered(
        (plugins.ENGINES, "duckdb", "helios_cortex.engines.duckdb_engine:DuckDBEngine"),
        (plugins.ENGINES, "duckdb", "other_package:DuckDBEngine"),
    )
    with pytest.raises(plugins.PluginError, match="registered more than once"):
        plugins.load(plugins.ENGINES, "duckdb")


def test_an_unknown_group_is_a_typo_not_an_empty_list():
    with pytest.raises(ValueError, match="unknown plugin group 'helios.engine'"):
        plugins.names("helios.engine")


def test_settings_load_without_their_plugins_and_the_host_can_check_them(registered):
    registered((plugins.ENGINES, "duckdb", "helios_cortex.engines.duckdb_engine:DuckDBEngine"))
    settings = Settings(
        connections={
            "local": {"engine": "duckdb", "path": ":memory:"},
            "cdw": {"engine": "impala", "host": "warehouse.example", "user": "svc", "password": "hunter2"},
        },
        catalogs={"default": {"catalog": "atlas", "base_url": "https://atlas.example", "user": "svc", "password": "hunter2"}},
    )
    assert plugins.missing_configured(settings) == (
        "no plugin 'impala' in helios.engines (used by connection 'cdw'); installed: duckdb; install helios-cloudera",
        "no plugin 'atlas' in helios.catalogs (used by catalog 'default'); installed: none; install helios-cloudera",
    )
    with pytest.raises(plugins.PluginNotFound) as raised:
        plugins.require_configured(settings)
    assert str(raised.value).count("\n") == 1
    assert "hunter2" not in str(raised.value)


def test_settings_whose_plugins_are_installed_pass(registered):
    registered((plugins.ENGINES, "duckdb", "helios_cortex.engines.duckdb_engine:DuckDBEngine"))
    plugins.require_configured(Settings(connections={"local": {"engine": "duckdb"}}))
