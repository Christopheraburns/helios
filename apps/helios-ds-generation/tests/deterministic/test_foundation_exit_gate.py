"""F-12 exit gate: TPC-DS -> planner -> deterministic plan -> Iceberg generation
manifest, run from clean state twice, must be identical."""

import pytest

from helios_ds.lifecycle import DatasetLifecycle, DatasetState
from helios_ds.manifests import manifest_key
from helios_ds.object_store import DeterminismIntegrityError, S3ObjectStore
from helios_ds.pipeline import plan_and_publish
from helios_ds.schemas import ScenarioPlanRecord
from helios_ds.templates import TemplateRegistry
from helios_ds.tpcds import DuckDbTpcds


def _published(sink, dataset_id):
    datasets = sink.read_dataset("helios_ds.datasets", dataset_id)
    plans = sorted(
        (p.model_dump() for p in sink.read_dataset("helios_ds.scenario_plans", dataset_id)),
        key=lambda p: p["scenario_id"],
    )
    templates = sorted(
        (t.model_dump() for t in sink.read_dataset("helios_ds.template_versions", dataset_id)),
        key=lambda t: t["template_id"],
    )
    artifacts = sorted(
        (a.artifact_id, a.sha256, a.size_bytes)
        for a in sink.read_dataset("helios_ds.artifacts", dataset_id)
    )
    from helios_ds.ground_truth import TRUTH_TABLES

    truth = {
        table: sorted(r.model_dump_json() for r in sink.read_dataset(table, dataset_id))
        for table in TRUTH_TABLES
    }
    return [d.model_dump() for d in datasets], plans, templates, artifacts, truth


def test_two_clean_environments_produce_identical_results(
    make_env, tiny_config, small_scale_factor
):
    results = []
    for label in ("env_a", "env_b"):
        # Everything fresh: regenerated TPC-DS, templates, catalog and object store.
        repo = DuckDbTpcds.generate(small_scale_factor)
        sink, store = make_env(label)
        result = plan_and_publish(tiny_config, repo, TemplateRegistry.load(), sink, store)
        results.append(
            (
                result,
                store.get(manifest_key(result.dataset_id)),
                _published(sink, result.dataset_id),
            )
        )

    (a, manifest_a, rows_a), (b, manifest_b, rows_b) = results
    assert a.dataset_id == b.dataset_id
    assert manifest_a == manifest_b
    assert a.manifest_sha256 == b.manifest_sha256
    for rows in (rows_a, rows_b):
        for datasets in rows[0]:
            datasets.pop("manifest_locator")  # the store root differs per environment
    assert rows_a == rows_b
    assert a.state is b.state is DatasetState.IN_REVIEW


def test_rerun_is_a_no_op_reproducibility_check(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    first = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    before = _published(sink, first.dataset_id)
    second = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert (second.dataset_id, second.manifest_sha256) == (first.dataset_id, first.manifest_sha256)
    assert not second.newly_published
    assert _published(sink, first.dataset_id) == before
    assert [e.state for e in DatasetLifecycle(sink).history(first.dataset_id)] == [
        "CREATING",
        "VALIDATING",
        "IN_REVIEW",
    ]
    runs = sink.read_dataset("helios_ds.generation_runs", first.dataset_id)
    assert sorted(r.outcome for r in runs) == ["SUCCEEDED", "SUCCEEDED"]


def test_tampered_manifest_fails_the_rerun_without_overwrite(
    make_env, tiny_config, small_repo, templates
):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    path = store.root / manifest_key(result.dataset_id)
    path.write_bytes(b"tampered")
    with pytest.raises(DeterminismIntegrityError):
        plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert path.read_bytes() == b"tampered"
    runs = sink.read_dataset("helios_ds.generation_runs", result.dataset_id)
    assert sorted(r.outcome for r in runs) == ["FAILED", "SUCCEEDED"]


def test_partial_publish_is_cleaned_up_on_retry(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    expected = _published(sink, result.dataset_id)

    # Simulate a crash after child rows were written but before the datasets row.
    sink2, store2 = make_env("b")
    stray = ScenarioPlanRecord(
        dataset_id=result.dataset_id,
        scenario_id="stray",
        scenario_type="x",
        business_key="k",
        rank_score="r",
        scenario_seed="s",
        source_refs=[],
        facts={},
        artifact_plan=[],
    )
    sink2.append("helios_ds.scenario_plans", [stray])
    DatasetLifecycle(sink2).transition(result.dataset_id, DatasetState.CREATING, "test")

    retried = plan_and_publish(tiny_config, small_repo, templates, sink2, store2)
    assert retried.newly_published
    rows = _published(sink2, result.dataset_id)
    rows[0][0].pop("manifest_locator"), expected[0][0].pop("manifest_locator")
    assert rows == expected


def test_prefixed_s3_store_publishes_validates_and_approves(
    make_env, fake_s3, tiny_config, small_repo, templates
):
    """Regression: an S3 locator's key includes the store prefix, so validation
    must look the manifest up by its logical key (failed in Workbench 2026-09-29)."""
    sink, _ = make_env("s3")
    client = fake_s3()
    store = S3ObjectStore(client, "applied-ai-buk-d5eff1ab", "helios-db/source")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert result.state is DatasetState.IN_REVIEW
    key = f"helios-db/source/_manifests/{result.dataset_id}/generation-manifest.json"
    assert ("applied-ai-buk-d5eff1ab", key) in client.objects
    assert not plan_and_publish(tiny_config, small_repo, templates, sink, store).newly_published
    DatasetLifecycle(sink).approve(result.dataset_id, store, "user:erin")
    assert DatasetLifecycle(sink).visible_datasets() == [result.dataset_id]


def test_failed_validation_run_recovers_on_retry(
    make_env, fake_s3, tiny_config, small_repo, templates, monkeypatch
):
    """A run that failed validation after publishing its rows is retried cleanly."""
    from helios_ds import pipeline

    sink, store = make_env("retry")
    monkeypatch.setattr(pipeline, "validate_published", lambda *a: ["simulated failure"])
    with pytest.raises(Exception, match="simulated failure"):
        plan_and_publish(tiny_config, small_repo, templates, sink, store)
    monkeypatch.undo()

    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert result.state is DatasetState.IN_REVIEW
    assert not result.newly_published  # rows from the failed run are reused
    assert [e.state for e in DatasetLifecycle(sink).history(result.dataset_id)] == [
        "CREATING",
        "VALIDATING",
        "FAILED",
        "CREATING",
        "VALIDATING",
        "IN_REVIEW",
    ]
    assert len(sink.read_dataset("helios_ds.datasets", result.dataset_id)) == 1


def test_pipeline_renders_every_renderable_artifact(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("r")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert result.rendered == {"chat": 3, "email": 3, "pdf": 3}
    assert result.pending == {}
    artifacts = sink.read_dataset("helios_ds.artifacts", result.dataset_id)
    assert len(artifacts) == 9
    for a in artifacts:
        key = a.source_locator["key"]
        assert (
            key == f"datasets/{result.dataset_id}/artifacts/{a.artifact_id}.{key.rsplit('.', 1)[1]}"
        )
        assert store.get(key) is not None
    sources = sink.read_dataset("helios_ds.artifact_sources", result.dataset_id)
    assert {s.artifact_id for s in sources} == {a.artifact_id for a in artifacts}

    again = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert again.rendered == result.rendered
    assert len(sink.read_dataset("helios_ds.artifacts", result.dataset_id)) == 9
    assert len(sink.read_dataset("helios_ds.artifact_sources", result.dataset_id)) == len(sources)


def test_tampered_artifact_fails_the_rerun(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("t")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    artifact = sink.read_dataset("helios_ds.artifacts", result.dataset_id)[0]
    path = store.root / artifact.source_locator["key"]
    path.write_bytes(b"tampered")
    with pytest.raises(DeterminismIntegrityError):
        plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert path.read_bytes() == b"tampered"


def test_artifact_types_without_renderers_are_reported_pending(make_env, small_repo, templates):
    from helios_ds.config import ArtifactConfig, DatasetConfig, ScenarioConfig

    config = DatasetConfig(
        artifacts={"pdf": ArtifactConfig(target_count=2), "image": ArtifactConfig(target_count=2)},
        scenarios={"product_return_damage": ScenarioConfig(weight=1.0)},
    )
    sink, store = make_env("p")
    result = plan_and_publish(config, small_repo, templates, sink, store)
    assert result.rendered == {"pdf": 2}
    assert result.pending == {"image": 2}
    assert result.state is DatasetState.IN_REVIEW
