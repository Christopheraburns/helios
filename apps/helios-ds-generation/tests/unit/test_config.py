import pytest
from pydantic import ValidationError

from helios_ds.config import DatasetConfig, DifficultyConfig, default_config


def test_default_config_is_valid():
    config = default_config()
    assert DatasetConfig.model_validate(config.model_dump()) == config


def test_tiny_fixture_config_is_valid(tiny_config_dict):
    DatasetConfig.model_validate(tiny_config_dict)


def test_equivalent_configs_hash_identically(tiny_config_dict):
    reordered = dict(reversed(list(tiny_config_dict.items())))
    reordered["artifacts"] = dict(reversed(list(reordered["artifacts"].items())))
    explicit_defaults = {**tiny_config_dict, "difficulty": DifficultyConfig().model_dump()}

    h = DatasetConfig.model_validate(tiny_config_dict).config_hash()
    assert DatasetConfig.model_validate(reordered).config_hash() == h
    assert DatasetConfig.model_validate(explicit_defaults).config_hash() == h


def test_canonical_json_is_compact_and_sorted(tiny_config_dict):
    text = DatasetConfig.model_validate(tiny_config_dict).canonical_json()
    assert " " not in text
    assert text.index('"artifacts"') < text.index('"master_seed"') < text.index('"scenarios"')


@pytest.mark.parametrize(
    "change",
    [
        {"master_seed": 43},
        {"tpcds_scale_factor": 10},
        {"artifacts": {"pdf": {"enabled": True, "target_count": 4}}},
    ],
)
def test_semantic_changes_change_hash(tiny_config_dict, change):
    base = DatasetConfig.model_validate(tiny_config_dict).config_hash()
    assert DatasetConfig.model_validate({**tiny_config_dict, **change}).config_hash() != base


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ({"unknown_field": 1}, "unknown_field"),
        ({"config_schema_version": "2.0"}, "config_schema_version"),
        ({"artifacts": {"hologram": {"target_count": 1}}}, "hologram"),
        ({"artifacts": {"pdf": {"target_count": 1, "colour": "red"}}}, "colour"),
        ({"scenarios": {"alien_invasion": {"weight": 0.5}}}, "alien_invasion"),
        ({"scenarios": {"product_return_damage": {"weight": 0.0}}}, "positive weight"),
        ({"difficulty": {"direct_identifier": 0.9}}, "sum to 1.0"),
    ],
)
def test_invalid_config_fails_clearly(tiny_config_dict, bad, fragment):
    with pytest.raises(ValidationError) as exc:
        DatasetConfig.model_validate({**tiny_config_dict, **bad})
    assert fragment in str(exc.value)


def test_scenario_weights_are_relative():
    weights = default_config().normalized_scenario_weights()
    assert list(weights) == sorted(weights)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["product_return_damage"] == pytest.approx(0.25 / 0.60)
