"""C-05: alias and contextual mentions, recorded with their difficulty tier."""

from collections import defaultdict

import pytest

from helios_ds.ground_truth import TRUTH_TABLES
from helios_ds.pipeline import plan_and_publish
from helios_ds.render.base import TIERS, MentionSpec, TextBuilder, compose


@pytest.fixture
def truth(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("tiers")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    tables = {t.split(".")[1]: sink.read_dataset(t, result.dataset_id) for t in TRUTH_TABLES}
    artifacts = sink.read_dataset("helios_ds.artifacts", result.dataset_id)
    return tables, artifacts


def test_every_mention_has_a_tier_and_every_story_has_all_three(truth):
    tables, artifacts = truth
    mentions = tables["entity_mentions"]
    assert mentions and all(m.difficulty in TIERS for m in mentions)
    by_scenario = defaultdict(set)
    for m in mentions:
        by_scenario[m.scenario_id].add(m.difficulty)
    assert set(by_scenario) == {a.scenario_id for a in artifacts}
    assert all(tiers == set(TIERS) for tiers in by_scenario.values()), by_scenario


def test_email_and_chat_artifacts_each_carry_all_three_tiers(truth):
    tables, artifacts = truth
    kinds = {a.artifact_id: a.artifact_type for a in artifacts}
    tiers = defaultdict(set)
    for m in tables["entity_mentions"]:
        tiers[m.artifact_id].add(m.difficulty)
    for artifact_id, kind in kinds.items():
        if kind in ("email", "chat"):
            assert tiers[artifact_id] == set(TIERS), (kind, tiers[artifact_id])


def test_alias_and_contextual_mentions_resolve_to_the_same_entities_as_direct_ones(truth):
    tables, _ = truth
    entities = {e.entity_id: e for e in tables["entities"]}
    per_scenario = defaultdict(lambda: defaultdict(set))
    for m in tables["entity_mentions"]:
        assert m.entity_id in entities
        per_scenario[m.scenario_id][m.difficulty].add(m.entity_id)
    for tiers in per_scenario.values():
        # Aliases and contextual references never introduce entities of their own.
        assert (tiers["alias"] | tiers["contextual"]) <= tiers["direct"]


def test_alias_surfaces_differ_from_canonical_names(truth):
    tables, _ = truth
    entities = {e.entity_id: e for e in tables["entities"]}
    for m in tables["entity_mentions"]:
        if m.difficulty != "direct":
            assert m.surface_form not in entities[m.entity_id].canonical_name, m


def test_unknown_tier_is_rejected():
    with pytest.raises(ValueError, match="tier"):
        compose(
            TextBuilder(), "{@x}", {}, {"x": MentionSpec("it", "Item", {"table": "item"}, "vague")}
        )
