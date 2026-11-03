"""helios-cloudera's settings: Impala and Atlas options, and the Workbench profile.

Parity: WorkbenchSettings must resolve on Workbench exactly what the code before
Settings did, so existing deployments keep their variables (burndown 3.4).
"""

import os
from importlib.util import find_spec
from pathlib import Path

import pytest
from pydantic import ValidationError

from helios_core.artifacts import ArtifactStore
from helios_core.runs import RunStore
from helios_core.settings import Settings

from helios_cloudera.atlas import atlas_config, atlas_config_for
from helios_cloudera.impala import impala_config, impala_config_for
from helios_cloudera.workbench import WorkbenchSettings

PREFIXES = ("HELIOS_", "IMPALA_", "WORKLOAD_", "ATLAS_", "INFERENCE_", "ANTHROPIC_", "MISTRAL_", "CDSW_")

# The LLM client comes with helios-core[llm].
needs_llm = pytest.mark.skipif(find_spec("httpx") is None, reason="needs helios-core[llm]")

JDBC = "jdbc:impala://vw.example.com:443/default;AuthMech=12;transportMode=http;httpPath=cliservice/vw;ssl=1"


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    """No inherited configuration, and a working directory without config files."""
    for name in list(os.environ):
        if name.startswith(PREFIXES) or name == "LLM_PROVIDER":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


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
    assert WorkbenchSettings().connections == {}
    s = Settings(connections={"w": {"engine": "impala", "host": "vw"}})  # loads ...
    with pytest.raises(ValidationError):
        impala_config_for(s.connections["w"])                            # ... fails when used
    with pytest.raises(ValueError):
        impala_config_for(Settings(connections={"d": {"engine": "duckdb"}}).connections["d"])


def test_the_plugin_reads_the_secret_settings_keep_hidden():
    s = Settings(connections={"w": {"engine": "impala", "host": "vw", "user": "u", "password": "hunter2"}})
    assert "hunter2" not in repr(s)
    assert impala_config_for(s.connections["w"]).password == "hunter2"


def test_nested_helios_variables_merge_with_conventional_ones(monkeypatch):
    monkeypatch.setenv("IMPALA_HOST", "vw.example.com")
    monkeypatch.setenv("WORKLOAD_USER", "svc")
    monkeypatch.setenv("WORKLOAD_PASSWORD", "pw")
    monkeypatch.setenv("HELIOS_CONNECTIONS__DEFAULT__DATABASE", "sales")
    monkeypatch.setenv("HELIOS_LLM__TIMEOUT_SECONDS", "12")
    s = WorkbenchSettings()
    default = s.connections["default"]
    assert (default.engine, default.host, default.user, default.database) == ("impala", "vw.example.com", "svc", "sales")
    assert s.llm.timeout_seconds == 12


# Parity: in the environments Helios runs in today, the stores and clients built from
# Settings resolve exactly what the code before Settings resolved. EXPECTED was
# recorded by running that code (commit ef2b6f5 plus 1.3) in each environment.
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


EXPECTED = {'workbench': {'metadata_db': '/home/cdsw/helios/state/helios.db',
               'runs_dir': '/home/cdsw/helios/runs',
               'artifacts_dir': '/home/cdsw/helios',
               'evidence_index': '/home/cdsw/helios/state/evidence.lance',
               'audit_retention_days': 90,
               'impala': ['vw.example.com',
                          443,
                          'svc-helios',
                          'pw',
                          'default',
                          'cliservice/vw',
                          'impersonate'],
               'atlas': ['https://dl.example.com/atlas/api/atlas/v2', 'atlas', 'pw', False],
               'llm': ['openai', 'llama', 'jwt', 'https://caii.example.com/v1', 30.0]},
 'workbench-without-project-dir': {'metadata_db': '/home/cdsw/helios/state/helios.db',
                                   'runs_dir': '/home/cdsw/helios/runs',
                                   'artifacts_dir': '/home/cdsw/helios',
                                   'evidence_index': '/home/cdsw/helios/state/evidence.lance',
                                   'audit_retention_days': 30,
                                   'impala': None,
                                   'atlas': None,
                                   'llm': None},
 'local': {'metadata_db': '/var/lib/helios.db',
           'runs_dir': '/var/runs',
           'artifacts_dir': '/srv/helios',
           'evidence_index': '/var/evidence.lance',
           'audit_retention_days': 30,
           'impala': ['vw.example.com', 28000, 'me', 'pw', 'tpcds', 'cliservice', 'service'],
           'atlas': None,
           'llm': ['anthropic', 'claude-sonnet-5', 'sk-ant', None, 45.0]},
 'mistral': {'metadata_db': '/srv/helios/state/helios.db',
             'runs_dir': '/srv/helios/runs',
             'artifacts_dir': '/srv/helios',
             'evidence_index': '/srv/helios/state/evidence.lance',
             'audit_retention_days': 30,
             'impala': None,
             'atlas': None,
             'llm': ['mistral', 'mistral-large', 'mk', 'https://api.mistral.ai/v1', 30.0]},
 'provider-without-key': {'metadata_db': '/srv/helios/state/helios.db',
                          'runs_dir': '/srv/helios/runs',
                          'artifacts_dir': '/srv/helios',
                          'evidence_index': '/srv/helios/state/evidence.lance',
                          'audit_retention_days': 30,
                          'impala': None,
                          'atlas': None,
                          'llm': None}}


@needs_llm
@pytest.mark.parametrize("name", ENVIRONMENTS)
def test_parity_with_the_code_before_settings(monkeypatch, name):
    from helios_core.llm import llm_from_settings

    for key, value in ENVIRONMENTS[name].items():
        monkeypatch.setenv(key, value)
    s = WorkbenchSettings()
    expected = EXPECTED[name]

    assert str(s.metadata_db) == expected["metadata_db"]
    assert RunStore.from_settings(s).runs_dir == expected["runs_dir"]
    assert ArtifactStore.from_settings(s).root == Path(expected["artifacts_dir"])
    assert str(s.evidence.index) == expected["evidence_index"]
    assert s.audit_retention_days == expected["audit_retention_days"]

    if expected["impala"] is None:
        assert "default" not in s.connections
    else:
        c = impala_config_for(s.connections["default"])
        assert [c.host, c.port, c.user, c.password, c.database, c.http_path, c.identity.value] == expected["impala"]

    if expected["atlas"] is None:
        assert "default" not in s.catalogs
    else:
        a = atlas_config_for(s.catalogs["default"])
        assert [a.base_url, a.user, a.password, a.verify_ssl] == expected["atlas"]

    llm = llm_from_settings(s.llm)
    if expected["llm"] is None:
        assert llm is None
    else:
        assert [llm.provider, llm.model, llm.api_key, llm.base_url, llm.timeout] == expected["llm"]


def test_impala_and_atlas_lookups():
    s = Settings(
        connections={
            "default": {"engine": "impala", "host": "vw", "user": "u", "password": "p"},
            "local": {"engine": "duckdb", "path": "/x.duckdb"},
        },
        catalogs={"default": {"catalog": "atlas", "base_url": "https://dl/atlas/", "user": "a", "password": "p"}},
    )
    assert impala_config(s).host == "vw"
    assert impala_config(s, "unknown-ref").host == "vw"   # falls back to default
    assert impala_config(s, "local") is None              # not an Impala connection
    assert impala_config(Settings()) is None
    assert atlas_config(s).base_url == "https://dl/atlas"
    assert atlas_config(Settings()) is None


@pytest.mark.parametrize(("chooser", "provider", "base_url"), [
    ({}, "openai", "https://caii.example.com/v1"),                              # nothing else: Cloudera AI Inference
    ({"LLM_PROVIDER": "openai"}, "openai", "https://caii.example.com/v1"),
    ({"ANTHROPIC_API_KEY": "sk-ant"}, "anthropic", None),                       # a key wins, and keeps its own URL
    ({"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "mk"}, "mistral", "https://api.mistral.ai/v1"),
])
def test_inference_variables_apply_only_when_nothing_else_chooses_the_llm(monkeypatch, chooser, provider, base_url):
    monkeypatch.setenv("INFERENCE_BASE_URL", "https://caii.example.com/v1")
    monkeypatch.setenv("INFERENCE_TOKEN", "jwt")
    for key, value in chooser.items():
        monkeypatch.setenv(key, value)
    llm = WorkbenchSettings().llm
    assert (llm.provider, llm.base_url) == (provider, base_url)
    assert (llm.api_key.get_secret_value() == "jwt") is (provider == "openai")


def test_helios_root_still_beats_the_workbench_project_dir(monkeypatch):
    monkeypatch.setenv("CDSW_PROJECT_DIR", "/home/cdsw")
    assert WorkbenchSettings().data_dir == Path("/home/cdsw/helios")
    monkeypatch.setenv("HELIOS_ROOT", "/srv/helios")
    assert WorkbenchSettings().data_dir == Path("/srv/helios")
