"""A dataset's audit trail (task R-07, ADR 0001).

Nothing new is stored: the trail is reconstructed from the two append-only logs,
helios_ds.dataset_lifecycle (state changes, approvals, rejections, superseding)
and helios_ds.review_marks (accept, flag, comment). It covers the dataset's own
events and the superseding events its approval caused on other datasets in
its lineage. Approval and rejection events also carry the review counts as they
stood at that moment, recomputed from the marks.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from .lakehouse import LakehouseSink
from .lifecycle import DatasetLifecycle, DatasetState
from .review import ReviewStore, reduce_marks, summarize
from .schemas import ArtifactRecord, DatasetLifecycleRecord, ReviewMarkRecord

DECISIONS = (DatasetState.READY.value, DatasetState.REJECTED.value)


@dataclass
class AuditEvent:
    occurred_at: str
    kind: str  # "lifecycle" or "review"
    action: str  # lifecycle state, or ACCEPTED / FLAGGED / COMMENT
    actor: str
    dataset_id: str  # the dataset the event changed
    artifact_id: Optional[str] = None
    note: Optional[str] = None
    related_dataset_id: Optional[str] = None
    event_seq: Optional[int] = None
    review_counts: Optional[Dict[str, int]] = None


def dataset_history(sink: LakehouseSink, dataset_id: str) -> List[AuditEvent]:
    """Every recorded event about ``dataset_id``, oldest first."""
    lifecycle = DatasetLifecycle(sink)
    marks = [
        m
        for m in sink.read_dataset(ReviewStore.TABLE, dataset_id)
        if isinstance(m, ReviewMarkRecord)
    ]
    artifact_ids = [
        a.artifact_id
        for a in sink.read_dataset("helios_ds.artifacts", dataset_id)
        if isinstance(a, ArtifactRecord)
    ]

    events = [_lifecycle_event(e) for e in lifecycle.history(dataset_id)]
    for other in lifecycle.lineage(dataset_id):
        if other != dataset_id:
            events += [
                _lifecycle_event(e)
                for e in lifecycle.history(other)
                if e.related_dataset_id == dataset_id
            ]
    for event in events:
        if event.dataset_id == dataset_id and event.action in DECISIONS:
            before = [m for m in marks if m.created_at <= event.occurred_at]
            reviews: Dict[str, List[ReviewMarkRecord]] = {}
            for m in before:
                reviews.setdefault(m.artifact_id, []).append(m)
            event.review_counts = summarize(
                {a: reduce_marks(ms) for a, ms in reviews.items()}, artifact_ids
            )
    events += [
        AuditEvent(
            occurred_at=m.created_at,
            kind="review",
            action=m.status,
            actor=m.reviewer,
            dataset_id=m.dataset_id,
            artifact_id=m.artifact_id,
            note=m.note,
        )
        for m in marks
    ]
    # Lifecycle events sort before review marks at the same instant, and by
    # event_seq among themselves.
    return sorted(
        events,
        key=lambda e: (e.occurred_at, e.kind != "lifecycle", e.event_seq or 0, e.artifact_id or ""),
    )


def _lifecycle_event(event: DatasetLifecycleRecord) -> AuditEvent:
    return AuditEvent(
        occurred_at=event.occurred_at,
        kind="lifecycle",
        action=event.state,
        actor=event.actor,
        dataset_id=event.dataset_id,
        note=event.reason,
        related_dataset_id=event.related_dataset_id,
        event_seq=event.event_seq,
    )
