import random

from helios_ds.config import SCENARIO_TYPES
from helios_ds.ids import GENERATOR_SCHEMA_VERSION
from helios_ds.scenarios import ScenarioPlanner


def _select(records, seed=42):
    planner = ScenarioPlanner(master_seed=seed, generator_version=GENERATOR_SCHEMA_VERSION)
    return planner.select_candidates(records, "product_return_damage", target_count=5, weight=1.0)


def test_shuffled_input_selects_same_population(tiny_store_returns):
    expected = _select(tiny_store_returns)
    for shuffle_seed in range(5):
        shuffled = list(tiny_store_returns)
        random.Random(shuffle_seed).shuffle(shuffled)
        assert _select(shuffled) == expected


def test_selection_is_stable_across_runs(tiny_store_returns):
    assert [_select(tiny_store_returns) for _ in range(3)] == [_select(tiny_store_returns)] * 3


def test_master_seed_changes_selection(tiny_store_returns):
    assert _select(tiny_store_returns, seed=42) != _select(tiny_store_returns, seed=43)


def test_planner_and_config_agree_on_scenario_types():
    assert set(ScenarioPlanner.SCENARIO_TEMPLATES) == set(SCENARIO_TYPES)
