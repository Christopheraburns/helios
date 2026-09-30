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


# --- R-06: approve, reject, supersede ------------------------------------------


def _clone_ready(sink, dataset_id, clone_id, config_hash=None):
    """Record another dataset (same config unless given) that is already READY."""
    from helios_ds.schemas import DatasetLifecycleRecord

    record = sink.read_dataset("helios_ds.datasets", dataset_id)[0]
    update = {"dataset_id": clone_id}
    if config_hash:
        update["config_hash"] = config_hash
    sink.append("helios_ds.datasets", [record.model_copy(update=update)])
    sink.append(
        "helios_ds.dataset_lifecycle",
        [
            DatasetLifecycleRecord(
                dataset_id=clone_id, event_seq=i, state=state, actor="t", occurred_at=f"2020-0{i}"
            )
            for i, state in enumerate(["CREATING", "VALIDATING", "IN_REVIEW", "READY"], start=1)
        ],
    )


def test_approval_supersedes_ready_datasets_in_the_lineage_only(
    make_env, tiny_config, small_repo, templates
):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    _clone_ready(sink, result.dataset_id, "old")
    _clone_ready(sink, result.dataset_id, "other-lineage", config_hash="f" * 64)
    lifecycle = DatasetLifecycle(sink)
    assert lifecycle.lineage(result.dataset_id) == sorted([result.dataset_id, "old"])

    approval = lifecycle.approve(result.dataset_id, store, "alice", "looks good")
    assert approval.record.state == "READY" and approval.record.reason == "looks good"
    assert approval.superseded == ["old"]
    assert lifecycle.state("old") is S.SUPERSEDED
    superseded = lifecycle.history("old")[-1]
    assert (superseded.actor, superseded.related_dataset_id) == ("alice", result.dataset_id)
    assert lifecycle.state("other-lineage") is S.READY
    assert lifecycle.visible_datasets() == sorted([result.dataset_id, "other-lineage"])
    # Re-running the superseding step (an interrupted approval) changes nothing.
    assert lifecycle.supersede_lineage(result.dataset_id, "alice") == []
    with pytest.raises(InvalidTransition):
        lifecycle.transition("old", S.CREATING, "t")


def test_reject_needs_a_reason_and_is_terminal(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    lifecycle = DatasetLifecycle(sink)
    with pytest.raises(InvalidTransition, match="reason"):
        lifecycle.reject(result.dataset_id, "bob", "  ")
    with pytest.raises(InvalidTransition, match="actor"):
        lifecycle.reject(result.dataset_id, "", "bad tone")
    with pytest.raises(InvalidTransition, match="actor"):
        lifecycle.approve(result.dataset_id, store, " ")
    record = lifecycle.reject(result.dataset_id, "bob", "chat tone is off")
    assert (record.state, record.actor, record.reason) == ("REJECTED", "bob", "chat tone is off")
    with pytest.raises(InvalidTransition):
        lifecycle.approve(result.dataset_id, store, "bob")
    assert lifecycle.visible_datasets() == []


def test_decision_states_are_not_reachable_through_transition(make_env):
    sink, _ = make_env("a")
    lifecycle = DatasetLifecycle(sink)
    for state in (S.CREATING, S.VALIDATING, S.IN_REVIEW):
        lifecycle.transition("d", state, "t")
    for state in (S.READY, S.REJECTED, S.SUPERSEDED):
        with pytest.raises(InvalidTransition):
            lifecycle.transition("d", state, "t")


def test_regenerating_a_superseded_dataset_is_a_reproducibility_check(
    make_env, tiny_config, small_repo, templates
):
    sink, store = make_env("a")
    first = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    lifecycle = DatasetLifecycle(sink)
    lifecycle.approve(first.dataset_id, store, "alice")
    sink.append(
        "helios_ds.dataset_lifecycle",
        [
            lifecycle.history(first.dataset_id)[-1].model_copy(
                update={"event_seq": 5, "state": "SUPERSEDED", "related_dataset_id": "newer"}
            )
        ],
    )
    again = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    assert again.dataset_id == first.dataset_id and not again.newly_published
    assert lifecycle.state(first.dataset_id) is S.SUPERSEDED
