import shutil

import pytest
import yaml
from pydantic import ValidationError

from helios_ds.config import ARTIFACT_TYPES
from helios_ds.scenarios import SCENARIOS, expected_scenario_columns
from helios_ds.templates import TemplateRegistry, default_templates_dir


def test_every_scenario_artifact_has_a_matching_template(templates):
    for definition in SCENARIOS.values():
        for artifact_type, template_id in definition.artifacts.items():
            spec = templates.get(template_id).spec
            assert spec.artifact_type == artifact_type
            assert definition.scenario_type in spec.supported_scenario_types


def test_template_fields_exist_in_scenario_records(templates, small_repo):
    for scenario_type in SCENARIOS:
        columns = set(expected_scenario_columns(scenario_type, small_repo))
        for template in templates.templates.values():
            spec = template.spec
            if scenario_type in spec.supported_scenario_types:
                missing = set(spec.required_fields + spec.optional_fields) - columns
                assert not missing, f"{spec.template_id} needs {missing} from {scenario_type}"


def test_every_artifact_type_has_a_template(templates):
    assert {t.spec.artifact_type for t in templates.templates.values()} == set(ARTIFACT_TYPES)


def test_bundle_hash_is_stable(templates):
    assert TemplateRegistry.load().bundle_hash() == templates.bundle_hash()


def _copy_templates(tmp_path):
    root = tmp_path / "templates"
    shutil.copytree(default_templates_dir(), root)
    return root


def test_changing_any_template_byte_changes_hashes(tmp_path, templates):
    root = _copy_templates(tmp_path)
    (root / "pdf" / "return_report" / "layout.py").write_text("# new renderer asset\n")
    changed = TemplateRegistry.load(root)
    assert changed.get("return_report").content_hash != templates.get("return_report").content_hash
    assert changed.get("damaged_item").content_hash == templates.get("damaged_item").content_hash
    assert changed.bundle_hash() != templates.bundle_hash()


def test_unknown_descriptor_field_is_rejected(tmp_path):
    root = _copy_templates(tmp_path)
    path = root / "chat" / "support_chat" / "template.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["colour"] = "red"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValidationError, match="colour"):
        TemplateRegistry.load(root)


def test_directory_must_match_descriptor(tmp_path):
    root = _copy_templates(tmp_path)
    shutil.move(str(root / "chat" / "support_chat"), str(root / "email" / "support_chat"))
    with pytest.raises(ValueError, match="<artifact_type>/<template_id>"):
        TemplateRegistry.load(root)
