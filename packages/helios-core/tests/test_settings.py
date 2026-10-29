"""helios_core.settings: resolution order, defaults, and parity with the code it replaces."""

import importlib
import os
from pathlib import Path

import platformdirs
import pytest
from pydantic import ValidationError

from helios_core import audit, config, runs
from helios_core._pending import evidence
from helios_core.artifacts import ArtifactStore
from helios_core.atlas import atlas_config_for
from helios_core.engines.impala import impala_config_for
from helios_core.llm.client import llm_from_env
from helios_core.metadata.sqlite import default_database_path
from helios_core.settings import Settings

PREFIXES = ("HELIOS_", "IMPALA_", "WORKLOAD_", "ATLAS_", "INFERENCE_", "ANTHROPIC_", "MISTRAL_", "CDSW_")

JDBC = "jdbc:impala://vw.example.com:443/default;AuthMech=12;transportMode=http;httpPath=cliservice/vw;ssl=1"


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


def test_nested_helios_variables_merge_with_conventional_ones(monkeypatch):
    monkeypatch.setenv("IMPALA_HOST", "vw.example.com")
    monkeypatch.setenv("WORKLOAD_USER", "svc")
    monkeypatch.setenv("WORKLOAD_PASSWORD", "pw")
    monkeypatch.setenv("HELIOS_CONNECTIONS__DEFAULT__DATABASE", "sales")
    monkeypatch.setenv("HELIOS_LLM__TIMEOUT_SECONDS", "12")
    s = Settings()
    default = s.connections["default"]
    assert (default.engine, default.host, default.user, default.database) == ("impala", "vw.example.com", "svc", "sales")
    assert s.llm.timeout_seconds == 12


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


def test_impala_jdbc_url_supplies_port_and_http_path_unless_given():
    s = Settings(connections={"w": {"engine": "impala", "host": JDBC, "user": "u", "password": "p"}})
    cfg = impala_config_for(s.connections["w"])
    assert (cfg.host, cfg.port, cfg.http_path) == ("vw.example.com", 443, "cliservice/vw")
    s = Settings(connections={"w": {
        "engine": "impala", "host": JDBC, "user": "u", "password": "p", "port": 21050, "http_path": "x"}})
    cfg = impala_config_for(s.connections["w"])
    assert (cfg.port, cfg.http_path) == (21050, "x")


def test_plugins_validate_their_own_options(monkeypatch):
    monkeypatch.setenv("IMPALA_HOST", "vw")   # no credentials: no default connection, as before
    assert Settings().connections == {}
    s = Settings(connections={"w": {"engine": "impala", "host": "vw"}})  # loads ...
    with pytest.raises(ValidationError):
        impala_config_for(s.connections["w"])                            # ... fails when used
    with pytest.raises(ValueError):
        impala_config_for(Settings(connections={"d": {"engine": "duckdb"}}).connections["d"])


@pytest.mark.parametrize(("raw", "days"), [("45", 45), ("0", 1), ("99999", 3650), ("soon", 30)])
def test_audit_retention_is_clamped_like_before(monkeypatch, raw, days):
    monkeypatch.setenv("HELIOS_AUDIT_RETENTION_DAYS", raw)
    assert Settings().audit_retention_days == days == audit.retention_days()


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
    assert impala_config_for(s.connections["w"]).password == "hunter2"


def test_settings_are_immutable():
    with pytest.raises(ValidationError):
        Settings().runs_dir = Path("/x")


# Parity: for the environments Helios runs in today, Settings resolves exactly what
# the code it replaces resolves, so wiring it in (burndown 1.4) changes nothing.
ENVIRONMENTS = {
    "workbench": {
        "CDSW_PROJECT_DIR": "/home/cdsw",
        "IMPALA_HOST": JDBC,
        "WORKLOAD_USER": "svc-helios",
        "WORKLOAD_PASSWORD": "pw",
        "IMPALA_PROXY_DELEGATION": "true",
        "ATLAS_BASE": "https://dl.example.com/atlas/api/atlas/v2/",
        "ATLAS_USER": "atlas",
        "ATLAS_PASS": "pw",
        "ATLAS_VERIFY_SSL": "false",
        "INFERENCE_BASE_URL": "https://caii.example.com/v1",
        "INFERENCE_TOKEN": "jwt",
        "INFERENCE_MODEL": "llama",
        "HELIOS_AUDIT_RETENTION_DAYS": "90",
    },
    "workbench-without-project-dir": {"CDSW_PROJECT_ID": "abc123"},
    "local": {
        "HELIOS_ROOT": "/srv/helios",
        "HELIOS_METADATA_DB": "/var/lib/helios.db",
        "HELIOS_RUNS_DIR": "/var/runs",
        "HELIOS_EVIDENCE_INDEX": "/var/evidence.lance",
        "IMPALA_HOST": "vw.example.com:21050",
        "IMPALA_PORT": "28000",
        "IMPALA_DATABASE": "tpcds",
        "WORKLOAD_USER": "me",
        "WORKLOAD_PASSWORD": "pw",
        "ANTHROPIC_API_KEY": "sk-ant",
        "MISTRAL_API_KEY": "ignored-anthropic-wins",
        "HELIOS_LLM_TIMEOUT_SECONDS": "45",
    },
    "mistral": {
        "HELIOS_ROOT": "/srv/helios",
        "LLM_PROVIDER": "mistral",
        "MISTRAL_API_KEY": "mk",
        "MISTRAL_MODEL": "mistral-large",
        "ANTHROPIC_API_KEY": "present-but-not-chosen",
    },
    "provider-without-key": {"HELIOS_ROOT": "/srv/helios", "LLM_PROVIDER": "anthropic"},
}


@pytest.mark.parametrize("name", ENVIRONMENTS)
def test_parity_with_the_code_it_replaces(monkeypatch, name):
    for key, value in ENVIRONMENTS[name].items():
        monkeypatch.setenv(key, value)
    s = Settings()

    assert str(s.metadata_db) == default_database_path()
    assert str(s.evidence.index) == evidence.location()
    assert s.artifacts_dir == ArtifactStore().root
    monkeypatch.setattr(runs, "ROOT", runs.ROOT)       # restored after the reload below
    monkeypatch.setattr(runs, "RUNS_DIR", runs.RUNS_DIR)
    assert str(s.runs_dir) == importlib.reload(runs).RUNS_DIR
    assert s.audit_retention_days == audit.retention_days()

    old_impala = config.impala_config()
    if old_impala is None:
        assert "default" not in s.connections
    else:
        assert impala_config_for(s.connections["default"]) == old_impala

    old_atlas = config.atlas_config()
    if old_atlas is None:
        assert "default" not in s.catalogs
    else:
        assert atlas_config_for(s.catalogs["default"]) == old_atlas

    old_llm = llm_from_env()
    if old_llm is None:
        assert s.llm.provider is None
    else:
        key = s.llm.api_key.get_secret_value() if s.llm.api_key else None
        assert (s.llm.provider, s.llm.model, key, s.llm.base_url, s.llm.timeout_seconds) == (
            old_llm.provider, old_llm.model, old_llm.api_key, old_llm.base_url, old_llm.timeout)
