"""Review marks (task R-03, ADR 0001): advisory per-artifact review.

Marks are append-only rows in helios_ds.review_marks. An artifact's review status
is its latest ACCEPTED or FLAGGED mark (COMMENT marks add notes without changing
status). Flags are advisory: they never block approval.
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

from .lakehouse import LakehouseSink
from .schemas import ReviewMarkRecord

TABLE = "helios_ds.review_marks"
STATUSES = ("ACCEPTED", "FLAGGED", "COMMENT")
UNREVIEWED = "UNREVIEWED"


class InvalidMark(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactReview:
    status: str = UNREVIEWED
    marks: List[ReviewMarkRecord] = field(default_factory=list)

    @property
    def comment_count(self) -> int:
        return sum(1 for m in self.marks if m.note)

    @property
    def last_mark_at(self) -> Optional[str]:
        return self.marks[-1].created_at if self.marks else None


def reduce_marks(marks: Iterable[ReviewMarkRecord]) -> ArtifactReview:
    review = ArtifactReview()
    for mark in sorted(marks, key=lambda m: (m.created_at, m.mark_id)):
        review.marks.append(mark)
        if mark.status in ("ACCEPTED", "FLAGGED"):
            review.status = mark.status
    return review


class ReviewStore:
    TABLE = TABLE

    def __init__(self, sink: LakehouseSink, clock: Callable[[], str] = utc_now):
        self.sink = sink
        self.clock = clock

    def add(
        self,
        dataset_id: str,
        artifact_id: str,
        status: str,
        reviewer: str,
        comment: Optional[str] = None,
    ) -> ReviewMarkRecord:
        status = status.upper()
        comment = (comment or "").strip() or None
        if status not in STATUSES:
            raise InvalidMark(f"status must be one of {', '.join(STATUSES)}")
        if status in ("FLAGGED", "COMMENT") and not comment:
            raise InvalidMark(f"a {status.lower()} mark needs a comment")
        if not reviewer:
            raise InvalidMark("a review mark needs an authenticated reviewer")
        mark = ReviewMarkRecord(
            dataset_id=dataset_id,
            mark_id=uuid.uuid4().hex,
            artifact_id=artifact_id,
            status=status,
            note=comment[:4000] if comment else None,
            reviewer=reviewer,
            created_at=self.clock(),
        )
        self.sink.append(TABLE, [mark])
        return mark

    def for_dataset(self, dataset_id: str) -> Dict[str, ArtifactReview]:
        by_artifact: Dict[str, List[ReviewMarkRecord]] = {}
        for mark in self.sink.read_dataset(TABLE, dataset_id):
            assert isinstance(mark, ReviewMarkRecord)
            by_artifact.setdefault(mark.artifact_id, []).append(mark)
        return {artifact_id: reduce_marks(marks) for artifact_id, marks in by_artifact.items()}

    def for_artifact(self, artifact_id: str) -> ArtifactReview:
        marks = [
            m
            for m in self.sink.read(TABLE, where={"artifact_id": artifact_id})
            if isinstance(m, ReviewMarkRecord)
        ]
        return reduce_marks(marks)


def summarize(reviews: Dict[str, ArtifactReview], artifact_ids: Iterable[str]) -> Dict[str, int]:
    """Counts by status over a dataset's artifacts (unmarked ones are UNREVIEWED)."""
    ids = list(artifact_ids)
    counts = Counter(reviews.get(a, ArtifactReview()).status for a in ids)
    return {
        "total": len(ids),
        "unreviewed": counts[UNREVIEWED],
        "accepted": counts["ACCEPTED"],
        "flagged": counts["FLAGGED"],
        "comments": sum(reviews[a].comment_count for a in ids if a in reviews),
    }
