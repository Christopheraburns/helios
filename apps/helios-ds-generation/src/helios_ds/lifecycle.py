"""Dataset lifecycle and publish validation (spec: "publish protocol").

States: CREATING -> VALIDATING -> IN_REVIEW -> READY, with FAILED and REJECTED
as exits. State is an append-only event log in helios_ds.dataset_lifecycle;
the current state is the event with the highest event_seq. Only READY datasets
are crawler-visible, and READY is reachable only through ``approve()``, which
re-validates the published dataset first.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, FrozenSet, List, Optional

from .lakehouse import LakehouseSink
from .manifests import GenerationManifest, manifest_key
from .object_store import ObjectStore, sha256_hex
from .schemas import (
    DatasetLifecycleRecord,
    DatasetRecord,
    ScenarioPlanRecord,
    TemplateVersionRecord,
)


class DatasetState(str, Enum):
    CREATING = "CREATING"
    VALIDATING = "VALIDATING"
    IN_REVIEW = "IN_REVIEW"
    READY = "READY"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


TRANSITIONS: Dict[Optional[DatasetState], FrozenSet[DatasetState]] = {
    None: frozenset({DatasetState.CREATING}),
    DatasetState.CREATING: frozenset({DatasetState.VALIDATING, DatasetState.FAILED}),
    DatasetState.VALIDATING: frozenset({DatasetState.IN_REVIEW, DatasetState.FAILED}),
    DatasetState.IN_REVIEW: frozenset(
        {DatasetState.READY, DatasetState.REJECTED, DatasetState.FAILED}
    ),
    DatasetState.FAILED: frozenset({DatasetState.CREATING}),  # retry the same dataset
    DatasetState.READY: frozenset(),
    DatasetState.REJECTED: frozenset(),
}


class InvalidTransition(RuntimeError):
    pass


class ValidationFailed(RuntimeError):
    def __init__(self, dataset_id: str, problems: List[str]):
        super().__init__(f"dataset {dataset_id} failed validation: " + "; ".join(problems))
        self.problems = problems


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_published(sink: LakehouseSink, store: ObjectStore, dataset_id: str) -> List[str]:
    """Problems that must block a dataset from leaving VALIDATING. Empty = valid."""
    problems: List[str] = []
    datasets = sink.read_dataset("helios_ds.datasets", dataset_id)
    if len(datasets) != 1:
        return [f"expected exactly 1 datasets row, found {len(datasets)}"]
    record = datasets[0]
    assert isinstance(record, DatasetRecord)

    # Look the manifest up by its logical key: locators hold backend-specific
    # addresses (an S3 locator's key already includes the store prefix).
    data = store.get(manifest_key(dataset_id))
    if data is None:
        return [f"manifest object {manifest_key(dataset_id)} is missing"]
    if store.locator(manifest_key(dataset_id)) != record.manifest_locator:
        problems.append("datasets.manifest_locator does not match the object store")
    if sha256_hex(data) != record.manifest_sha256:
        return ["manifest object hash does not match datasets.manifest_sha256"]
    manifest = GenerationManifest.from_bytes(data)
    if manifest.dataset_id != dataset_id:
        problems.append(f"manifest belongs to dataset {manifest.dataset_id}")

    plans = sink.read_dataset("helios_ds.scenario_plans", dataset_id)
    plan_ids = sorted(p.scenario_id for p in plans if isinstance(p, ScenarioPlanRecord))
    if plan_ids != sorted(s.scenario_id for s in manifest.scenarios):
        problems.append(
            f"scenario_plans has {len(plan_ids)} rows; "
            f"manifest has {len(manifest.scenarios)} scenarios"
        )
    if record.scenario_count != len(manifest.scenarios):
        problems.append("datasets.scenario_count does not match the manifest")
    if record.planned_artifact_count != sum(len(s.artifacts) for s in manifest.scenarios):
        problems.append("datasets.planned_artifact_count does not match the manifest")

    templates = sink.read_dataset("helios_ds.template_versions", dataset_id)
    stored = sorted(
        (t.template_id, t.content_hash) for t in templates if isinstance(t, TemplateVersionRecord)
    )
    if stored != sorted((t["template_id"], t["content_hash"]) for t in manifest.templates):
        problems.append("template_versions rows do not match the manifest")
    return problems


class DatasetLifecycle:
    TABLE = "helios_ds.dataset_lifecycle"

    def __init__(self, sink: LakehouseSink, clock: Callable[[], str] = utc_now):
        self.sink = sink
        self.clock = clock

    def history(self, dataset_id: str) -> List[DatasetLifecycleRecord]:
        events = [
            e
            for e in self.sink.read_dataset(self.TABLE, dataset_id)
            if isinstance(e, DatasetLifecycleRecord)
        ]
        return sorted(events, key=lambda e: e.event_seq)

    def state(self, dataset_id: str) -> Optional[DatasetState]:
        events = self.history(dataset_id)
        return DatasetState(events[-1].state) if events else None

    def _append(
        self,
        dataset_id: str,
        to: DatasetState,
        actor: str,
        run_id: Optional[str],
        reason: Optional[str],
    ) -> DatasetLifecycleRecord:
        events = self.history(dataset_id)
        current = DatasetState(events[-1].state) if events else None
        if to not in TRANSITIONS[current]:
            raise InvalidTransition(
                f"{dataset_id}: {current and current.value} -> {to.value} not allowed"
            )
        record = DatasetLifecycleRecord(
            dataset_id=dataset_id,
            event_seq=len(events) + 1,
            state=to.value,
            run_id=run_id,
            actor=actor,
            occurred_at=self.clock(),
            reason=reason,
        )
        self.sink.append(self.TABLE, [record])
        return record

    def transition(
        self,
        dataset_id: str,
        to: DatasetState,
        actor: str,
        run_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> DatasetLifecycleRecord:
        if to is DatasetState.READY:
            raise InvalidTransition("READY is reachable only through approve()")
        return self._append(dataset_id, to, actor, run_id, reason)

    def approve(
        self, dataset_id: str, store: ObjectStore, approver: str, reason: Optional[str] = None
    ) -> DatasetLifecycleRecord:
        """IN_REVIEW -> READY, only if the published dataset still validates."""
        if self.state(dataset_id) is not DatasetState.IN_REVIEW:
            raise InvalidTransition(f"{dataset_id} is not IN_REVIEW")
        problems = validate_published(self.sink, store, dataset_id)
        if problems:
            raise ValidationFailed(dataset_id, problems)
        return self._append(dataset_id, DatasetState.READY, approver, None, reason)

    def visible_datasets(self) -> List[str]:
        """Dataset IDs whose current state is READY (what the crawler may see)."""
        latest: Dict[str, DatasetLifecycleRecord] = {}
        for event in self.sink.read(self.TABLE):
            assert isinstance(event, DatasetLifecycleRecord)
            if (
                event.dataset_id not in latest
                or event.event_seq > latest[event.dataset_id].event_seq
            ):
                latest[event.dataset_id] = event
        return sorted(d for d, e in latest.items() if e.state == DatasetState.READY.value)
