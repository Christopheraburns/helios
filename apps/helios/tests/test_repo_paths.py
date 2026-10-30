"""Shipped files resolve from runtime.REPO; runtime state from Settings.data_dir."""
import json

from apps.helios import runtime
from apps.helios.console import assistant, evaluation, ontology


def test_shipped_files_resolve_from_the_repository():
    assert runtime.REPO.joinpath("pyproject.toml").is_file()
    assert evaluation.BUILTIN_SUITE.is_file()
    evaluation._validate_suite(json.loads(evaluation.BUILTIN_SUITE.read_text()))
    assert ontology.ONTOLOGY_DIR.joinpath("core", "core.yaml").is_file()
    for relative, _slug, _title in assistant.DOC_SOURCES:
        assert (assistant.REPO_ROOT / relative).is_file(), relative


def test_user_eval_suites_come_from_the_data_dir(monkeypatch, tmp_path):
    suites = tmp_path / "state" / "eval-suites"
    suites.mkdir(parents=True)
    (suites / "mine.json").write_text("{}")
    monkeypatch.setenv("HELIOS_DATA_DIR", str(tmp_path))

    assert evaluation._suite_paths() == [evaluation.BUILTIN_SUITE, suites / "mine.json"]
