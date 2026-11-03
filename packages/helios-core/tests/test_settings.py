"""helios_core.settings: resolution order and defaults.

The Impala and Atlas options, and parity with the code Settings replaced on
Workbench, are tested in helios-cloudera (test_cloudera_settings.py).
"""

import os
from pathlib import Path

import platformdirs
import pytest
from pydantic import ValidationError
from pydantic_settings import PydanticBaseSettingsSource

from helios_core.settings import Settings

PREFIXES = ("HELIOS_", "IMPALA_", "WORKLOAD_", "ATLAS_", "INFERENCE_", "ANTHROPIC_", "MISTRAL_", "CDSW_")


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    """No inherited configuration, and a working directory without config files."""
    for name in list(os.environ):
        if name.startswith(PREFIXES) or name == "LLM_PROVIDER":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def test_defaults_live_in_the_user_data_directory():
    s = Settings()
    root = platformdirs.user_data_path("helios")
    assert s.data_dir == root
    assert s.artifacts_dir == root
    assert s.runs_dir == root / "runs"
    assert s.metadata_db == root / "state" / "helios.db"
    assert s.evidence.index == root / "state" / "evidence.lance"
    assert s.connections == {} and s.catalogs == {} and s.llm.provider is None
    assert s.sqlite_journal_mode == "DELETE" and s.audit_retention_days == 30


def test_paths_follow_data_dir_unless_set(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HELIOS_RUNS_DIR", str(tmp_path / "elsewhere"))
    s = Settings()
    assert s.metadata_db == tmp_path / "data" / "state" / "helios.db"
    assert s.runs_dir == tmp_path / "elsewhere"


def test_resolution_order(monkeypatch, tmp_path):
    (tmp_path / "helios.toml").write_text(
        'sqlite_journal_mode = "TRUNCATE"\naudit_retention_days = 10\nruns_dir = "/file"\n'
        'data_dir = "/file-data"\n'
    )
    monkeypatch.setenv("HELIOS_ROOT", "/conventional")   # conventional environment
    monkeypatch.setenv("HELIOS_RUNS_DIR", "/env")         # HELIOS_* environment
    monkeypatch.setenv("HELIOS_AUDIT_RETENTION_DAYS", "20")
    s = Settings(audit_retention_days=40)                  # argument
    assert s.audit_retention_days == 40          # argument beats HELIOS_*
    assert s.runs_dir == Path("/env")            # HELIOS_* beats the file
    assert s.data_dir == Path("/conventional")   # conventional beats the file
    assert s.sqlite_journal_mode == "TRUNCATE"   # file beats the default


def test_core_reads_no_workbench_variables(monkeypatch):
    """The Cloudera and Workbench names belong to helios-cloudera's WorkbenchSettings."""
    for name, value in {
        "CDSW_PROJECT_DIR": "/home/cdsw", "CDSW_PROJECT_ID": "abc",
        "IMPALA_HOST": "vw", "WORKLOAD_USER": "svc", "WORKLOAD_PASSWORD": "pw",
        "ATLAS_BASE": "https://dl/atlas", "ATLAS_USER": "a", "ATLAS_PASS": "pw",
        "INFERENCE_BASE_URL": "https://caii.example.com/v1", "INFERENCE_TOKEN": "jwt",
    }.items():
        monkeypatch.setenv(name, value)
    s = Settings()
    assert (s.connections, s.catalogs, s.llm.provider) == ({}, {}, None)
    assert s.data_dir == platformdirs.user_data_path("helios")


def test_a_profile_source_sits_between_the_environment_and_the_config_file(monkeypatch):
    class Profile(PydanticBaseSettingsSource):
        def get_field_value(self, field, field_name):
            return None, field_name, False

        def __call__(self):
            return {"data_dir": "/profile", "audit_retention_days": 7, "connections": {"default": {"engine": "duckdb"}}}

    class ProfileSettings(Settings):
        @classmethod
        def profile_sources(cls, settings_cls):
            return (Profile(settings_cls),)

    Path("helios.toml").write_text('audit_retention_days = 99\nsqlite_journal_mode = "WAL"\n')
    monkeypatch.setenv("HELIOS_ROOT", "/root")                                 # conventional beats the profile
    monkeypatch.setenv("HELIOS_CONNECTIONS__DEFAULT__PATH", "/x.duckdb")       # HELIOS_* merges into it
    s = ProfileSettings()
    assert s.data_dir == Path("/root")
    assert s.audit_retention_days == 7                                          # the profile beats the file
    assert s.sqlite_journal_mode == "WAL"                                       # the file still applies
    assert (s.connections["default"].engine, s.connections["default"].options) == ("duckdb", {"path": "/x.duckdb"})
    monkeypatch.delenv("HELIOS_CONNECTIONS__DEFAULT__PATH")
    assert Settings().audit_retention_days == 99                                # plain Settings has no profile


def test_connections_of_any_engine_from_a_file(tmp_path):
    (tmp_path / "helios.toml").write_text(
        '[connections.warehouse]\nengine = "impala"\nhost = "vw"\nuser = "svc"\npassword = "pw"\n'
        '[connections.sales]\nengine = "sqlalchemy"\nurl = "postgresql+psycopg://helios:pw@db/sales"\n'
        '[connections.local]\nengine = "duckdb"\npath = "/data/tpcds.duckdb"\n'
        '[catalogs.lake]\ncatalog = "atlas"\nbase_url = "https://dl/atlas"\nuser = "a"\npassword = "pw"\n'
    )
    s = Settings()
    assert {name: c.engine for name, c in s.connections.items()} == {
        "warehouse": "impala", "sales": "sqlalchemy", "local": "duckdb"}
    assert s.connections["local"].options == {"path": "/data/tpcds.duckdb"}
    assert s.connections["sales"].url.get_secret_value() == "postgresql+psycopg://helios:pw@db/sales"
    assert s.catalogs["lake"].catalog == "atlas"


def test_connections_from_helios_variables(monkeypatch):
    monkeypatch.setenv("HELIOS_CONNECTIONS__SALES__ENGINE", "sqlalchemy")
    monkeypatch.setenv("HELIOS_CONNECTIONS__SALES__URL", "postgresql+psycopg://db/sales")
    sales = Settings().connections["sales"]
    assert sales.engine == "sqlalchemy" and sales.url.get_secret_value() == "postgresql+psycopg://db/sales"


def test_a_plugin_that_is_not_installed_still_loads_but_engine_is_required(tmp_path):
    (tmp_path / "helios.toml").write_text('[connections.snow]\nengine = "snowflake"\naccount = "x"\n')
    assert Settings().connections["snow"].options == {"account": "x"}
    (tmp_path / "helios.toml").write_text('[connections.snow]\naccount = "x"\n')
    with pytest.raises(ValidationError):
        Settings()


def test_pyproject_tool_helios_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.helios]\nruns_dir = "/from-pyproject"\n')
    assert Settings().runs_dir == Path("/from-pyproject")


def test_helios_toml_wins_over_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.helios]\nruns_dir = "/from-pyproject"\n')
    (tmp_path / "helios.toml").write_text('runs_dir = "/from-helios-toml"\n')
    assert Settings().runs_dir == Path("/from-helios-toml")


def test_explicit_config_file(monkeypatch, tmp_path):
    config_file = tmp_path / "conf" / "prod.toml"
    config_file.parent.mkdir()
    config_file.write_text('[connections.warehouse]\nengine = "duckdb"\npath = "/data/w.duckdb"\n')
    assert Settings.from_file(config_file).connections["warehouse"].path == "/data/w.duckdb"
    monkeypatch.setenv("HELIOS_CONFIG", str(config_file))
    assert Settings().connections["warehouse"].path == "/data/w.duckdb"
    monkeypatch.setenv("HELIOS_CONFIG", str(tmp_path / "missing.toml"))
    with pytest.raises(FileNotFoundError):
        Settings()


def test_unknown_keys_in_a_config_file_are_errors(tmp_path):
    (tmp_path / "helios.toml").write_text('run_dir = "/typo"\n')
    with pytest.raises(ValidationError):
        Settings()


def test_unrelated_helios_variables_are_ignored(monkeypatch):
    monkeypatch.setenv("HELIOS_MCP_TOKEN", "x")
    monkeypatch.setenv("HELIOS_GRAPH_TOKEN", "y")
    Settings()


@pytest.mark.parametrize(("raw", "days"), [("45", 45), ("0", 1), ("99999", 3650), ("soon", 30)])
def test_audit_retention_is_clamped_like_before(monkeypatch, raw, days):
    monkeypatch.setenv("HELIOS_AUDIT_RETENTION_DAYS", raw)
    assert Settings().audit_retention_days == days


def test_journal_mode_and_timeout_are_validated():
    assert Settings(sqlite_journal_mode="wal").sqlite_journal_mode == "WAL"
    with pytest.raises(ValidationError):
        Settings(sqlite_journal_mode="MEMORY")
    with pytest.raises(ValidationError):
        Settings(llm={"timeout_seconds": 0})


def test_secrets_are_not_printed():
    s = Settings(
        connections={
            "w": {"engine": "impala", "host": "vw", "user": "u", "password": "hunter2"},
            "pg": {"engine": "sqlalchemy", "url": "postgresql://u:hunter3@db/x", "ssl_token": "t0k"},
        },
        llm={"provider": "anthropic", "api_key": "sk-1"},
    )
    for secret in ("hunter2", "hunter3", "t0k", "sk-1"):
        assert secret not in repr(s)
    assert s.connections["w"].password.get_secret_value() == "hunter2"


def test_settings_are_immutable():
    with pytest.raises(ValidationError):
        Settings().runs_dir = Path("/x")


def test_connection_refs_resolve_by_name_then_default():
    s = Settings(connections={
        "default": {"engine": "impala", "host": "vw", "user": "u", "password": "p"},
        "sales": {"engine": "sqlalchemy", "url": "postgresql://db/sales"},
    })
    assert s.resolve_connection("sales") == "sales"
    assert s.resolve_connection("impala") == "default"            # recorded before named connections
    assert s.resolve_connection("env:IMPALA_HOST") == "default"
    assert s.resolve_connection(None) == "default"
    assert Settings().resolve_connection("sales") is None          # nothing configured
    assert Settings(connections={"sales": {"engine": "duckdb"}}).resolve_connection("other") is None
