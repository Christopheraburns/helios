import random

import pytest

from helios_ds.config import SCENARIO_TYPES, ArtifactConfig, DatasetConfig, ScenarioConfig
from helios_ds.scenarios import SCENARIOS, ScenarioPlanner, allocate, rank_candidates

DATASET = "23a5b752-0b04-5ef7-afad-ca7205a1c999"


def _config(**overrides) -> DatasetConfig:
    base = {
        "master_seed": 42,
        "artifacts": {
            "pdf": ArtifactConfig(target_count=7),
            "email": ArtifactConfig(target_count=5),
            "chat": ArtifactConfig(target_count=6),
            "image": ArtifactConfig(target_count=4),
            "audio": ArtifactConfig(target_count=3),
            "video": ArtifactConfig(target_count=2),
        },
        "scenarios": {
            "product_return_damage": ScenarioConfig(weight=0.25),
            "warehouse_inventory_issue": ScenarioConfig(weight=0.20),
            "promotion_performance": ScenarioConfig(weight=0.15),
        },
    }
    return DatasetConfig(**{**base, **overrides})


def _plan(repo, templates, config=None):
    return ScenarioPlanner(config or _config(), templates).plan(repo, DATASET)


def test_planner_and_config_agree_on_scenario_types():
    assert set(SCENARIOS) == set(SCENARIO_TYPES)


def test_three_runs_produce_identical_plans(small_repo, templates):
    plans = [_plan(small_repo, templates).model_dump() for _ in range(3)]
    assert plans[0] == plans[1] == plans[2]


def test_shuffled_source_rows_produce_the_same_plan(small_repo, make_shuffled_repo, templates):
    tables = {t for d in SCENARIOS.values() for t in d.tables}
    shuffled = make_shuffled_repo(tables, seed=0.25)
    assert _plan(shuffled, templates) == _plan(small_repo, templates)


def test_planned_counts_equal_targets(small_repo, templates):
    plan = _plan(small_repo, templates)
    for artifact_type, counts in plan.artifact_counts.items():
        assert counts["planned"] == counts["target"], artifact_type
        assert counts["planned"] == sum(
            1 for s in plan.scenarios for a in s.artifacts if a.artifact_type == artifact_type
        )


def test_allocation_follows_weights_with_largest_remainder():
    allocation, unallocated = allocate(_config())
    # pdf 7 over return/warehouse/promotion (0.25/0.20/0.15): quotas 2.92/2.33/1.75,
    # floors 2/2/1, the two leftovers go to the largest remainders (return, promotion)
    assert {s: a["pdf"] for s, a in allocation.items()} == {
        "product_return_damage": 3,
        "warehouse_inventory_issue": 2,
        "promotion_performance": 2,
    }
    # video only comes from warehouse scenarios, audio only from return scenarios here
    assert allocation["warehouse_inventory_issue"]["video"] == 2
    assert allocation["product_return_damage"]["audio"] == 3
    assert unallocated == {}


def test_artifact_type_without_a_supporting_scenario_is_reported(small_repo, templates):
    config = _config(scenarios={"promotion_performance": ScenarioConfig(weight=1.0)})
    plan = _plan(small_repo, templates, config)
    assert plan.artifact_counts["video"] == {"target": 2, "planned": 0}
    assert plan.artifact_counts["audio"] == {"target": 3, "planned": 0}


def test_shortfall_when_source_has_too_few_records(small_repo, templates):
    config = _config(
        artifacts={"pdf": ArtifactConfig(target_count=50)},
        scenarios={"promotion_performance": ScenarioConfig(weight=1.0)},
    )
    plan = _plan(small_repo, templates, config)
    counts = plan.scenario_counts["promotion_performance"]
    assert counts["requested"] == 50
    assert counts["planned"] == counts["eligible"] < 50
    assert plan.artifact_counts["pdf"]["planned"] == counts["eligible"]


def test_master_seed_changes_the_population(small_repo, templates):
    keys = {s.business_key for s in _plan(small_repo, templates).scenarios}
    other = {
        s.business_key for s in _plan(small_repo, templates, _config(master_seed=43)).scenarios
    }
    assert keys != other


def test_plan_ids_and_seeds_are_unique(small_repo, templates):
    plan = _plan(small_repo, templates)
    artifacts = [a for s in plan.scenarios for a in s.artifacts]
    assert len({s.scenario_id for s in plan.scenarios}) == len(plan.scenarios)
    assert len({a.artifact_id for a in artifacts}) == len(artifacts)
    assert len({a.artifact_seed for a in artifacts}) == len(artifacts)


def test_rank_candidates_ignores_input_order(tiny_store_returns):
    def key(record):
        return record["business_key"]

    expected = rank_candidates(tiny_store_returns, 42, "product_return_damage", key)
    for seed in range(5):
        shuffled = list(tiny_store_returns)
        random.Random(seed).shuffle(shuffled)
        assert rank_candidates(shuffled, 42, "product_return_damage", key) == expected


def test_duplicate_business_keys_are_rejected(tiny_store_returns):
    with pytest.raises(ValueError, match="duplicate business key"):
        rank_candidates(
            tiny_store_returns + tiny_store_returns[:1], 42, "x", lambda r: r["business_key"]
        )
