"""Relative file paths in a config file are relative to the file (burndown 7.3), so one
helios.toml means the same to the facade, the helios command and an agent's `helios mcp`
wherever each starts. Environment variables and values in code keep meaning the working
directory."""
from pathlib import Path

from helios_cortex.settings import Settings, anchored

BASE = Path("/project")


def test_each_kind_of_path_is_anchored_to_the_file():
    values = anchored({
        "data_dir": "helios-data", "artifacts_dir": "~/artifacts", "runs_dir": "/abs/runs",
        "evidence": {"index": "evidence.lance", "embedding_model": "x"},
        "connections": {"default": {"engine": "duckdb", "path": "warehouse.duckdb"},
                        "scratch": {"engine": "duckdb", "path": ":memory:"},
                        "lake": {"engine": "trino", "host": "trino.internal"}},
        "registry_url": "sqlite:///state/registry.db",
        "artifact_storage": {"location": "published"},
        "llm": {"provider": "anthropic"},
    }, BASE)
    assert values["data_dir"] == "/project/helios-data"
    assert values["artifacts_dir"] == str(Path("~/artifacts").expanduser())
    assert values["runs_dir"] == "/abs/runs"
    assert values["evidence"] == {"index": "/project/evidence.lance", "embedding_model": "x"}
    assert values["connections"]["default"]["path"] == "/project/warehouse.duckdb"
    assert values["connections"]["scratch"]["path"] == ":memory:"
    assert values["connections"]["lake"] == {"engine": "trino", "host": "trino.internal"}
    assert values["registry_url"] == "sqlite:////project/state/registry.db"
    assert values["artifact_storage"] == {"location": "/project/published"}
    assert values["llm"] == {"provider": "anthropic"}


def test_urls_and_absolute_paths_are_left_alone():
    values = {"registry_url": "postgresql+psycopg://db/helios", "artifact_storage": {"location": "s3://b/helios"},
              "connections": {"default": {"engine": "duckdb", "path": "/data/w.duckdb"}}}
    assert anchored(values, BASE) == values
    assert anchored({"registry_url": "sqlite:////abs/r.db"}, BASE) == {"registry_url": "sqlite:////abs/r.db"}
    assert anchored({"registry_url": "sqlite:///:memory:"}, BASE) == {"registry_url": "sqlite:///:memory:"}


def test_a_config_file_means_the_same_from_any_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    config = project / "helios.toml"
    config.write_text('data_dir = "helios-data"\n\n[connections.default]\nengine = "duckdb"\npath = "w.duckdb"\n')
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(project)
    found = Settings()                                          # ./helios.toml
    monkeypatch.chdir(elsewhere)
    named = Settings.from_file(config)
    monkeypatch.setenv("HELIOS_CONFIG", str(config))
    from_environment = Settings()
    for settings in (found, named, from_environment):
        assert settings.data_dir == project / "helios-data"
        assert settings.connections["default"].options["path"] == str(project / "w.duckdb")
        assert settings.registry_url.get_secret_value() == f"sqlite:///{project / 'helios-data/state/registry.db'}"


def test_the_environment_still_means_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HELIOS_DATA_DIR", "from-env")
    assert Settings().data_dir == Path("from-env")
