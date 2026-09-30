"""R-07: a dataset's history is reconstructed from the lifecycle and review logs."""

from helios_ds.audit import dataset_history
from helios_ds.lifecycle import DatasetLifecycle
from helios_ds.pipeline import plan_and_publish
from helios_ds.review import ReviewStore
from helios_ds.schemas import DatasetLifecycleRecord


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        self.t += 1
        return f"2030-01-01T00:00:{self.t:02d}+00:00"


def test_history_of_an_approved_dataset(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("a")
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    dataset_id = result.dataset_id
    clock = Clock()
    # An older approved dataset from the same config.
    record = sink.read_dataset("helios_ds.datasets", dataset_id)[0]
    sink.append("helios_ds.datasets", [record.model_copy(update={"dataset_id": "old"})])
    sink.append(
        "helios_ds.dataset_lifecycle",
        [
            DatasetLifecycleRecord(
                dataset_id="old", event_seq=i, state=s, actor="t", occurred_at="2020-01-01"
            )
            for i, s in enumerate(["CREATING", "VALIDATING", "IN_REVIEW", "READY"], start=1)
        ],
    )
    artifacts = [a.artifact_id for a in sink.read_dataset("helios_ds.artifacts", dataset_id)]
    reviews = ReviewStore(sink, clock)
    reviews.add(dataset_id, artifacts[0], "ACCEPTED", "alice")
    reviews.add(dataset_id, artifacts[1], "FLAGGED", "bob", "odd greeting")
    reviews.add(dataset_id, artifacts[1], "ACCEPTED", "alice")
    DatasetLifecycle(sink, clock).approve(dataset_id, store, "alice", "ship it")
    reviews.add(dataset_id, artifacts[2], "COMMENT", "carol", "after the fact")

    history = dataset_history(sink, dataset_id)
    lifecycle = [(e.action, e.dataset_id) for e in history if e.kind == "lifecycle"]
    assert lifecycle == [
        ("CREATING", dataset_id),
        ("VALIDATING", dataset_id),
        ("IN_REVIEW", dataset_id),
        ("READY", dataset_id),
        ("SUPERSEDED", "old"),
    ]
    approval = next(e for e in history if e.action == "READY")
    assert (approval.actor, approval.note) == ("alice", "ship it")
    assert approval.review_counts == {
        "total": len(artifacts),
        "unreviewed": len(artifacts) - 2,
        "accepted": 2,
        "flagged": 0,
        "comments": 1,
    }
    superseded = history[[e.action for e in history].index("SUPERSEDED")]
    assert superseded.related_dataset_id == dataset_id and superseded.actor == "alice"
    marks = [(e.action, e.actor, e.artifact_id) for e in history if e.kind == "review"]
    assert marks == [
        ("ACCEPTED", "alice", artifacts[0]),
        ("FLAGGED", "bob", artifacts[1]),
        ("ACCEPTED", "alice", artifacts[1]),
        ("COMMENT", "carol", artifacts[2]),
    ]
    # Oldest first, and the decision sits between the marks it followed and preceded.
    order = [e.action for e in history if e.occurred_at.startswith("2030")]
    assert order == ["ACCEPTED", "FLAGGED", "ACCEPTED", "READY", "SUPERSEDED", "COMMENT"]
