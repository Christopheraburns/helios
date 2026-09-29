"""Golden identifiers. These values must never change without a deliberate
GENERATOR_SCHEMA_VERSION bump: a failure here is a reproducibility regression."""

import random
import uuid

from helios_ds import ids

DATASET = ids.dataset_id("0" * 64)
SOURCE_KEY = "sr_item_sk=8129|sr_ticket_number=88291"
SCENARIO = ids.scenario_id(DATASET, "product_return_damage", SOURCE_KEY)


def test_golden_ids():
    assert DATASET == "23a5b752-0b04-5ef7-afad-ca7205a1c999"
    assert SCENARIO == "405c31a2-6c9c-5083-a8ee-0efd20cc5bd9"
    assert (
        ids.artifact_id(DATASET, SCENARIO, "pdf", 0, "1.0.0")
        == "92ea775d-4547-5487-a7a1-208a8990b5e2"
    )
    assert (
        ids.truth_entity_id(DATASET, "Item", "i_item_sk=8129")
        == "c6120fb2-30f6-577e-9e4b-0313c3c3b1b1"
    )
    assert (
        ids.claim_id(DATASET, SCENARIO, "PACKAGING_DAMAGED", "item:8129", "", 0)
        == "f8b05f5b-6f31-580b-8947-46c073941378"
    )


def test_golden_seeds_and_rng():
    d = ids.dataset_seed(42)
    s = ids.scenario_seed(d, "product_return_damage", SOURCE_KEY)
    a = ids.artifact_seed(s, "pdf", 0, "return_report", "1.0.0")
    assert d == "3d919379d6b6cd1443b1cac7f043cddc3458d644ce289d5c6967faf831e7de27"
    assert s == "d843ba807532f91ab562ac7f153a551382a1d8040fa8e3e59ebab24a862e6247"
    assert a == "efb6a3bde1a9923ed0805a59070897d7bb2ff56733e42405db81e50ba1417269"
    rng = ids.rng_for(a)
    assert [rng.randint(0, 999) for _ in range(5)] == [110, 998, 712, 952, 396]


def test_rng_ignores_global_random_state():
    seed = ids.artifact_seed("s", "pdf", 0, "t", "1")
    random.seed(1)
    first = ids.rng_for(seed).random()
    random.seed(2)
    assert ids.rng_for(seed).random() == first


def test_ids_below_dataset_are_namespaced_by_dataset():
    other = ids.dataset_id("1" * 64)
    assert ids.truth_entity_id(DATASET, "Item", "k") != ids.truth_entity_id(other, "Item", "k")
    other_scenario = ids.scenario_id(other, "product_return_damage", SOURCE_KEY)
    assert ids.artifact_id(DATASET, SCENARIO, "pdf", 0, "1") != ids.artifact_id(
        other, other_scenario, "pdf", 0, "1"
    )
    assert uuid.UUID(ids.artifact_id(DATASET, SCENARIO, "pdf", 0, "1")) == uuid.uuid5(
        uuid.UUID(DATASET), f"artifact:{SCENARIO}:pdf:0:1"
    )


def test_hash_parts_is_unambiguous():
    assert ids.hash_parts("ab", "c") != ids.hash_parts("a", "bc")
    assert ids.hash_parts(1) != ids.hash_parts("1")


def test_job_ids_are_not_deterministic():
    assert ids.generation_job_id() != ids.generation_job_id()


def test_id_generator_matches_functions():
    gen = ids.DeterministicIDGenerator(DATASET)
    assert gen.scenario("product_return_damage", SOURCE_KEY) == SCENARIO
    assert gen.artifact(SCENARIO, "pdf", 0, "1.0.0") == ids.artifact_id(
        DATASET, SCENARIO, "pdf", 0, "1.0.0"
    )
    assert gen.entity("Item", "i_item_sk=8129") == ids.truth_entity_id(
        DATASET, "Item", "i_item_sk=8129"
    )
