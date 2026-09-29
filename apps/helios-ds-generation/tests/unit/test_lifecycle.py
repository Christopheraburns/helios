import pytest

from helios_ds.lifecycle import DatasetLifecycle, DatasetState, InvalidTransition, ValidationFailed
from helios_ds.manifests import manifest_key
from helios_ds.pipeline import plan_and_publish

S = DatasetState


def test_happy_path_and_forbidden_transitions(make_env):
    sink, _ = make_env("a")
    lifecycle = DatasetLifecycle(sink)
    with pytest.raises(InvalidTransition):
        lifecycle.transition("d", S.VALIDATING, "t")
    lifecycle.transition("d", S.CREATING, "t")
    with pytest.raises(InvalidTransition):
        lifecycle.transition("d", S.IN_REVIEW, "t")
    lifecycle.transition("d", S.VALIDATING, "t")
    lifecycle.transition("d", S.IN_REVIEW, "t")
    with pytest.raises(InvalidTransition, match="approve"):
        lifecycle.transition("d", S.READY, "t")
    assert [e.state for e in lifecycle.history("d")] == ["CREATING", "VALIDATING", "IN_REVIEW"]
    assert [e.event_seq for e in lifecycle.history("d")] == [1, 2, 3]


def test_failed_dataset_can_retry_but_is_never_visible(make_env):
    sink, _ = make_env("a")
    lifecycle = DatasetLifecycle(sink)
    lifecycle.transition("d", S.CREATING, "t")
    lifecycle.transition("d", S.FAILED, "t", reason="boom")
    assert lifecycle.visible_datasets() == []
    lifecycle.transition("d", S.CREATING, "t")
    assert lifecycle.state("d") is S.CREATING


def test_only_approved_datasets_are_visible(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    lifecycle = DatasetLifecycle(sink)
    assert result.state is S.IN_REVIEW
    assert lifecycle.visible_datasets() == []
    lifecycle.approve(result.dataset_id, store, "user:erin")
    assert lifecycle.visible_datasets() == [result.dataset_id]
    assert lifecycle.history(result.dataset_id)[-1].actor == "user:erin"
    with pytest.raises(InvalidTransition):
        lifecycle.approve(result.dataset_id, store, "user:erin")


def test_approval_revalidates_the_published_dataset(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    path = store.root / manifest_key(result.dataset_id)
    path.chmod(0o644)
    path.write_bytes(b"{}")
    with pytest.raises(ValidationFailed, match="manifest object hash"):
        DatasetLifecycle(sink).approve(result.dataset_id, store, "user:erin")
    assert DatasetLifecycle(sink).state(result.dataset_id) is S.IN_REVIEW
