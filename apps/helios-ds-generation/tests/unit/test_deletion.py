"""Deleting datasets: objects and rows go, the lifecycle log stays."""

import pytest

from helios_ds.audit import dataset_history
from helios_ds.deletion import PURGED_TABLES, DatasetGone, delete_dataset
from helios_ds.lifecycle import DatasetLifecycle, DatasetState, InvalidTransition
from helios_ds.manifests import manifest_key
from helios_ds.object_store import S3ObjectStore, store_from_locator
from helios_ds.pipeline import plan_and_publish
from helios_ds.review import ReviewStore


def test_delete_removes_objects_and_rows_but_keeps_the_lifecycle(
    make_env, tiny_config, small_repo, templates
):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    dataset_id = result.dataset_id
    artifact = sink.read_dataset("helios_ds.artifacts", dataset_id)[0]
    ReviewStore(sink).add(dataset_id, artifact.artifact_id, "ACCEPTED", "alice")
    objects = [p for p in store.root.rglob("*") if p.is_file()]

    deleted = delete_dataset(sink, store_from_locator, dataset_id, "alice", "not needed")
    assert deleted.objects_deleted == len(objects) and not deleted.already_marked
    assert [p for p in store.root.rglob("*") if p.is_file()] == []
    for table in PURGED_TABLES:
        assert sink.read_dataset(table, dataset_id) == [], table
    lifecycle = DatasetLifecycle(sink)
    event = lifecycle.history(dataset_id)[-1]
    assert (event.state, event.actor, event.reason) == ("DELETED", "alice", "not needed")
    assert sink.read_dataset("helios_ds.generation_runs", dataset_id)
    assert [e.action for e in dataset_history(sink, dataset_id)][-1] == "DELETED"
    with pytest.raises(DatasetGone):
        delete_dataset(sink, store_from_locator, dataset_id, "alice")

    # Generating the same config again recreates the same dataset from scratch.
    again = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert again.dataset_id == dataset_id and again.state is DatasetState.IN_REVIEW
    assert [e.state for e in lifecycle.history(dataset_id)][-4:] == [
        "DELETED",
        "CREATING",
        "VALIDATING",
        "IN_REVIEW",
    ]
    assert store.get(manifest_key(dataset_id)) is not None


def test_an_interrupted_deletion_can_be_finished(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)

    class Broken:
        def __init__(self, locator, key):
            pass

        def delete_prefix(self, prefix):
            raise RuntimeError("AccessDenied")

    with pytest.raises(RuntimeError):
        delete_dataset(sink, Broken, result.dataset_id, "alice")
    lifecycle = DatasetLifecycle(sink)
    assert lifecycle.state(result.dataset_id) is DatasetState.DELETED
    assert lifecycle.visible_datasets() == []
    finished = delete_dataset(sink, store_from_locator, result.dataset_id, "alice")
    assert finished.already_marked and finished.objects_deleted > 0
    assert [e.state for e in lifecycle.history(result.dataset_id)].count("DELETED") == 1


def test_a_dataset_being_generated_cannot_be_deleted(make_env):
    sink, _ = make_env("a")
    lifecycle = DatasetLifecycle(sink)
    lifecycle.transition("d", DatasetState.CREATING, "t")
    with pytest.raises(InvalidTransition, match="being generated"):
        lifecycle.mark_deleted("d", "alice")
    with pytest.raises(InvalidTransition):
        lifecycle.transition("d", DatasetState.DELETED, "t")


def test_s3_delete_prefix_pages_and_stays_inside_the_prefix(fake_s3):
    client = fake_s3()
    store = S3ObjectStore(client, "bucket", "helios-db/source")
    for key in ["datasets/a/artifacts/1.pdf", "datasets/a/artifacts/2.eml", "datasets/a/x.json"]:
        store.put(key, key.encode())
    store.put("datasets/ab/artifacts/1.pdf", b"keep")
    client.objects[("bucket", "other/datasets/a/1")] = b"keep"
    assert store.delete_prefix("datasets/a") == 3  # three pages of two keys at most
    assert sorted(k for _, k in client.objects) == [
        "helios-db/source/datasets/ab/artifacts/1.pdf",
        "other/datasets/a/1",
    ]
